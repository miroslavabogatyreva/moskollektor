"""Карточка объекта и ряд показаний. Задача MOS-41 (Q4.4), приёмка М-08, Ф-91.

Ф-91 требует ряд показаний и ссылку на внешний источник (камеру), если он
заведён. Камеры (CCTV) в проекте нет и не будет: docs/HLD.md разд. 11.6
разбирает этот же шаг сценария ТЗ и закрывает его не интеграцией с камерами,
а чекбоксом «Проверено по внешним источникам» (pred.feedback.verified_externally,
задача Q5.8) — дешевле на три порядка и решение уже принято, здесь не
переигрываю. Этот метод закрывает свою часть Ф-91: ряд показаний за окно.

Ниже участка — GET /api/objects/{id}/channels (MOS-151, Q5.25, М-05): список
каналов с числом отказов, датой последнего и средней длительностью, чтобы
диспетчер видел не «риск по участку», а конкретный сыплющийся датчик. Путь
держит существующий префикс /api/objects, а не /sections из черновика задачи:
это одна и та же сущность, и разъезд названий уже разобран и закрыт в
docs/HLD.md разд. 3.4 (таблица переводов терминов раннего плана API) строкой
«GET /sections/{id} → GET /api/objects/{id}» — заводить второе имя для того
же участка значило бы переигрывать это решение.
"""
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/objects/{section_id}")
async def get_object(
    section_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("objects.read")),
):
    # last_reading_at — из smvu.reading, а не из feat.section_daily: свёртка
    # покрывает только 2025-07-01…2026-06-30, а у семи участков (554, 559, 564,
    # 567, 570, 574, 579) последнее показание — 03.03.2025, раньше этого окна.
    # Свёртка отдала бы null при живых данных. NULL здесь остаётся возможным
    # (участок без единой записи схема разрешает), сегодня таких нет ни одного.
    passport = await conn.fetchrow(
        """
        SELECT section_id, smvu_key, inventory_no,
               (SELECT max(read_time) FROM smvu.reading r WHERE r.section_id = x.section_id)
                   AS last_reading_at
        FROM ref.object_xref x
        WHERE section_id = $1
        """,
        section_id,
    )
    if passport is None:
        raise HTTPException(404, "объект не найден")

    channels = await conn.fetch(
        """
        SELECT channel_id, tag, name, system_kind, sensor_kind
        FROM smvu.channel
        WHERE section_id = $1 AND is_active
        ORDER BY channel_id
        """,
        section_id,
    )

    current_risk = await conn.fetchrow(
        """
        -- `as_of` у текущего риска — срез данных, `computed_at` у прогнозов ниже —
        -- время работы расчёта. До MOS-118 оба поля звались `computed_at`, и в одном
        -- ответе стояли два одинаковых имени с разным смыслом.
        --
        -- direction/explanation_ru берём LATERAL-подзапросом по последней ЗАПИСАННОЙ
        -- строке участка, а не по f.run_id = fc.run_id (нашла проверяющая, 21.09.2026,
        -- MOS-147 сломал связку молча). Политика журнала с того же MOS-147 пишет
        -- pred.forecast только когда решение изменилось — у 10 прогонов из 11 для
        -- данного участка строки нет вовсе, join по run_id находил направление
        -- и объяснение ровно 4 минуты в час из 60, остальное время — NULL с пустой
        -- подписью направления на экране. Журнал по построению хранит последнее
        -- записанное значение, и пропуск в нём значит «не менялось», а не «нет
        -- данных» — LATERAL это и читает: последняя строка участка по run_id, а не
        -- строка текущего прогона. Проверено по всем 3 173 участкам: 3 173 из 3 173
        -- получают и direction, и explanation_ru, а не 0 из 3 173, как было.
        SELECT fc.run_id, fc.probability, fc.risk_rank, fc.horizon_h,
               fc.as_of, fc.is_stale, p.direction, p.explanation_ru
        FROM pred.forecast_current fc
        LEFT JOIN LATERAL (
            SELECT direction, explanation_ru FROM pred.forecast f
             WHERE f.section_id = fc.section_id
             ORDER BY f.run_id DESC LIMIT 1
        ) p ON true
        WHERE fc.section_id = $1
        """,
        section_id,
    )

    recent_forecasts = await conn.fetch(
        """
        SELECT f.forecast_id, f.direction, f.probability, f.risk_rank,
               f.explanation_ru, r.started_at AS computed_at
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE f.section_id = $1
        ORDER BY r.started_at DESC
        LIMIT 10
        """,
        section_id,
    )

    return {
        **dict(passport),
        "channels": [dict(c) for c in channels],
        "current_risk": dict(current_risk) if current_risk else None,
        "recent_forecasts": [dict(f) for f in recent_forecasts],
    }


@router.get("/objects/{section_id}/readings")
async def get_object_readings(
    section_id: int,
    from_: date = Query(..., alias="from", description="дата начала окна, включительно"),
    to: date = Query(..., description="дата конца окна, включительно — весь день целиком"),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("objects.read")),
):
    """from/to обязательны и не моменты времени, а даты — обе границы включительны.

    Обязательность — не только про формат: smvu.reading партиционирована помесячно
    (313 млн строк, 109 партиций), запрос без границы по времени обходит их все.
    Расчётная сессия 16.09.2026 поймала на этом 62 секунды там, где ждала долей
    секунды. Верхняя граница в запросе — начало СЛЕДУЮЩЕГО за to дня, сравнение
    строгое: так «from=to=сегодня» отдаёт весь сегодняшний день, а не пустоту
    (та же ошибка на границе, что нашли в GET /api/forecasts, здесь исправлена сразу).
    """
    exists = await conn.fetchval("SELECT 1 FROM ref.object_xref WHERE section_id = $1", section_id)
    if exists is None:
        raise HTTPException(404, "объект не найден")

    to_exclusive = to + timedelta(days=1)
    rows = await conn.fetch(
        """
        SELECT read_time, channel_id, is_alarm, value_text, value_num
        FROM smvu.reading
        WHERE section_id = $1 AND read_time >= $2 AND read_time < $3
        ORDER BY read_time
        """,
        section_id, from_, to_exclusive,
    )
    return [dict(r) for r in rows]


@router.get("/objects/{section_id}/channels")
async def list_object_channels(
    section_id: int,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("objects.read")),
):
    """Каналы участка с фактом отказов — не прогноз, обычная арифметика по журналу.

    Отказ канала — эпизод smvu.fault_episode: закрытый (`ended_at IS NOT NULL`,
    незакрытый обрывает наработку и не сравним по длительности), длиннее часа
    (порог уже заложен при построении эпизодов, условие в запросе не холостое —
    держит определение видимым и сработает, если порог когда-нибудь сдвинут)
    и не год из `ref.app_setting.forecast_weight_exclude_year` (по умолчанию
    2021, заказчик 19.09.2026: переход СМВУ на новую версию раздул тревоги,
    этот год рекомендован к исключению из анализа). Год читаем из настройки,
    а не пишем числом в запросе: 21.09.2026 нашли ровно эту ловушку у другого
    умолчания — воркер считал по интервалу из одного места, проверка сверяла
    с другим, — и MOS-150 (db/migrations/028_section_weight.sql) завёл ту же
    настройку под этот же год для веса участка. Два места, один ключ. `0` в
    настройке значит «не исключать ни одного» — извлечённый год реальной даты
    никогда не равен 0, поэтому `<>` отключается сам, без отдельной ветки.

    `fault_value` в запросе не сужаем до «Неисправен»: у тепловых датчиков
    и датчиков температуры smvu.fault_rule признаёт отказом ещё и «Неопределен»,
    и оба участвуют в счётчике — так диспетчер видит все отказы канала, а не
    подмножество, выбранное под метрики модели (там сознательно уже, см.
    code/check_metrics.py).

    Проверено на живом стенде 21.09.2026 дважды и разными способами: методом
    (curl) — участок 257:269 (section_id 674) отдаёт пятёрку каналов
    42/41/39/34/33 отказа, числа и даты последнего отказа совпадают с таблицей
    в задаче MOS-151, участок 890:5 (section_id 2598) отдаёт все девять каналов
    с нулём отказов, а не пустой список; и экраном — карточка участка через
    mcp__playwright__* против того же стенда, таблица «Отказы по каналам»
    совпадает с curl, консоль чиста.

    total — отдельным COUNT(*) по smvu.channel, не оконной функцией: на MOS-135
    и в постраничности заявок оконная функция обнулялась за последней страницей,
    здесь этот запрос вообще не участвует в подсчёте отказов и не подвержен
    той же ловушке в принципе.

    ORDER BY заканчивается `c.channel_id` — без него у канала без отказов все
    три числа сортировки одинаковы (0, NULL, NULL), план сортировки становится
    недетерминированным и постраничность может отдать один канал дважды или
    пропустить его вовсе. Проверяющая 21.09.2026 нашла 1 268 участков с такой
    группой неразличимых каналов, крупнейшая — 81 канал на участке 2204.
    """
    exists = await conn.fetchval("SELECT 1 FROM ref.object_xref WHERE section_id = $1", section_id)
    if exists is None:
        raise HTTPException(404, "участок не найден")

    excl_year = await conn.fetchval(
        "SELECT coalesce(max(value) FILTER (WHERE key = 'forecast_weight_exclude_year'), 2021) "
        "FROM ref.app_setting"
    )
    total = await conn.fetchval("SELECT count(*) FROM smvu.channel WHERE section_id = $1", section_id)
    rows = await conn.fetch(
        """
        SELECT c.channel_id, c.system_kind, c.sensor_kind, c.name, c.is_active,
               count(e.*) AS faults_cnt,
               max(e.started_at) AS last_fault_at,
               round(avg(extract(epoch FROM e.ended_at - e.started_at)) / 3600.0, 1)
                   AS avg_duration_h
        FROM smvu.channel c
        LEFT JOIN smvu.fault_episode e ON e.channel_id = c.channel_id
            AND e.ended_at IS NOT NULL
            AND e.ended_at - e.started_at > interval '1 hour'
            AND extract(year FROM e.started_at) <> $2
        WHERE c.section_id = $1
        GROUP BY c.channel_id, c.system_kind, c.sensor_kind, c.name, c.is_active
        ORDER BY faults_cnt DESC, last_fault_at DESC NULLS LAST, avg_duration_h DESC NULLS LAST,
                 c.channel_id
        LIMIT $3 OFFSET $4
        """,
        section_id, excl_year, limit, offset,
    )
    return {"total": total, "items": [dict(r) for r in rows]}
