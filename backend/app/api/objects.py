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

from app.api.schemas import ObjectChannelList, ObjectDetail, ObjectReading, TreeCollector
from app.auth.deps import require, видимые_участки, проверить_участок
from app.db import get_conn

router = APIRouter(prefix="/api")

# Узлы дерева диспетчера и участки под ними (MOS-101, план 5.9). Принадлежность —
# по активным каналам: участок относится к КАЖДОМУ узлу и коллектору, где у него
# есть канал (у 564 участков из 3 173 узлов больше одного, у участка 1490 — два коллектора).
# Ось схемы по-прежнему раскладывает участок по большинству каналов
# (УЧАСТКИ_КОЛЛЕКТОРА в app.worker.run_v3) — дерево это правило не трогает,
# иначе участок пропадал бы из узла, где у него меньшинство каналов.
# Узлы без каналов на участках в дерево не попадают: на стенде таких 11 из 78 —
# диспетчерские пункты и шкафы ОПС, 275 каналов которых не привязаны к пикету.
УЗЛЫ_ДЕРЕВА = """
SELECT p.object_id AS collector_id, p.name AS collector_name,
       n.object_id, n.name, n.kind, count(*) AS channels,
       array_agg(DISTINCT c.section_id ORDER BY c.section_id) AS section_ids
  FROM smvu.channel c
  JOIN smvu.object_tree n ON n.object_id = c.object_id
  JOIN smvu.object_tree p ON p.object_id = n.parent_id AND p.level = 2
 WHERE c.is_active AND c.section_id IS NOT NULL
   AND ($1::int[] IS NULL OR c.section_id = ANY($1))
 GROUP BY p.object_id, p.name, n.object_id, n.name, n.kind
 ORDER BY p.object_id, n.name, n.object_id
"""


@router.get("/objects/tree", response_model=list[TreeCollector])
async def get_tree(
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("objects.read")),
):
    """Дерево «коллектор → узел» для экрана карты (5.9, М-05). Стоит ВЫШЕ
    /objects/{section_id}: иначе FastAPI сопоставил бы «tree» с целым section_id
    и ответил 422. Область видимости — та же, что у GET /api/risks (MOS-107):
    фильтр по участкам стоит в WHERE, поэтому узел без видимых участков и
    коллектор без видимых узлов выпадают сами, и число каналов у узла считается
    только по видимым участкам."""
    участки = await видимые_участки(user, conn)
    коллекторы: dict[int, dict] = {}
    for r in await conn.fetch(УЗЛЫ_ДЕРЕВА, участки):
        к = коллекторы.setdefault(
            r["collector_id"],
            {"object_id": r["collector_id"], "name": r["collector_name"], "nodes": []},
        )
        к["nodes"].append(
            {k: r[k] for k in ("object_id", "name", "kind", "channels", "section_ids")}
        )
    return list(коллекторы.values())


@router.get("/objects/{section_id}", response_model=ObjectDetail)
async def get_object(
    section_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("objects.read")),
):
    await проверить_участок(user, conn, section_id)
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

    dispatcher_objects = await conn.fetch(
        """
        SELECT DISTINCT n.object_id AS node_id, n.name AS node_name,
               p.object_id AS collector_id, p.name AS collector_name
          FROM smvu.channel c
          JOIN smvu.object_tree n ON n.object_id = c.object_id
          JOIN smvu.object_tree p ON p.object_id = n.parent_id AND p.level = 2
         WHERE c.section_id = $1 AND c.is_active
         ORDER BY p.object_id, n.object_id
        """,
        section_id,
    )

    return {
        **dict(passport),
        "channels": [dict(c) for c in channels],
        "dispatcher_objects": [dict(d) for d in dispatcher_objects],
        "current_risk": dict(current_risk) if current_risk else None,
        "recent_forecasts": [dict(f) for f in recent_forecasts],
    }


@router.get("/objects/{section_id}/readings", response_model=list[ObjectReading])
async def get_object_readings(
    section_id: int,
    from_: date = Query(..., alias="from", description="дата начала окна, включительно"),
    to: date = Query(..., description="дата конца окна, включительно — весь день целиком"),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("objects.read")),
):
    """from/to обязательны и не моменты времени, а даты — обе границы включительны.

    Обязательность — не только про формат: smvu.reading партиционирована помесячно
    (313 млн строк, 109 партиций), запрос без границы по времени обходит их все.
    Расчётная сессия 16.09.2026 поймала на этом 62 секунды там, где ждала долей
    секунды. Верхняя граница в запросе — начало СЛЕДУЮЩЕГО за to дня, сравнение
    строгое: так «from=to=сегодня» отдаёт весь сегодняшний день, а не пустоту
    (та же ошибка на границе, что нашли в GET /api/forecasts, здесь исправлена сразу).
    """
    await проверить_участок(user, conn, section_id)
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


@router.get("/objects/{section_id}/channels", response_model=ObjectChannelList)
async def list_object_channels(
    section_id: int,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("objects.read")),
):
    """Каналы участка с фактом отказов — не прогноз, обычная арифметика по журналу.

    Отказ канала — эпизод в определении модели v3, представление
    smvu.model_failure_event (038, MOS-153): значение из словаря D5
    (contracts/failure.v3.json — «Неисправен», «Батарея неисправна», «Много
    неисправных устройств», «Не определено»), длиннее часа, открытый эпизод
    считается — у него нет длительности, и в avg_duration_h он не входит.
    Эпизод начат не раньше нижней границы `pred.weight_window()` — по умолчанию
    2022-04-01, начало периода обучения модели v3 (MOS-159,
    db/migrations/032_section_weight_window.sql), — и до конца архива.
    Нижнюю границу читает та же функция, что у веса pred.section_weight: с 21.09.2026
    мы знаем цену двух умолчаний в двух местах. Верхнюю границу окна (2026-03-31)
    карточка НЕ берёт: вес не имеет права подглядывать в проверочное окно модели
    апрель–июнь, а карточка — факт журнала для диспетчера. С верхней границей f2
    22.09.2026 нашла на D5 148 каналов, у которых карточка писала «отказов не было»:
    участок 161, канал 334599 — 14 отказов апреля–июня, на карточке 0. Поэтому
    отказы карточки и веса совпадают только внутри окна веса. 2021 год, который
    заказчик 19.09.2026 рекомендовал исключить
    (переход СМВУ на новую версию раздул тревоги), лежит до нижней границы целиком;
    до 2022-04 парк другой, и отказы оттуда модель не видела.

    До 038 карточка считала экранные эпизоды smvu.fault_episode, где отказом
    идёт ещё и «Неопределен» тепловых датчиков и датчиков температуры. Теперь
    карточка, вес участка и метрики М-18…М-20 считают один и тот же отказ:
    карточка объясняет вес, а вес с другим определением она объяснить не может.
    Видимое изменение: эпизоды «Неопределен» с карточки ушли, появились
    «Много неисправных устройств».

    Проверено на живом стенде 21.09.2026, ещё на экранных эпизодах до 038, дважды и разными способами: методом
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
    await проверить_участок(user, conn, section_id)
    exists = await conn.fetchval("SELECT 1 FROM ref.object_xref WHERE section_id = $1", section_id)
    if exists is None:
        raise HTTPException(404, "участок не найден")

    total = await conn.fetchval("SELECT count(*) FROM smvu.channel WHERE section_id = $1", section_id)
    rows = await conn.fetch(
        """
        SELECT c.channel_id, c.system_kind, c.sensor_kind, c.name, c.is_active,
               count(e.*) AS faults_cnt,
               max(e.started_at) AS last_fault_at,
               round(avg(extract(epoch FROM e.ended_at - e.started_at)) / 3600.0, 1)
                   AS avg_duration_h
        FROM smvu.channel c
        CROSS JOIN pred.weight_window() w
        LEFT JOIN smvu.model_failure_event e ON e.channel_id = c.channel_id
            AND timezone('Europe/Moscow', e.started_at)::date >= w.date_from
        WHERE c.section_id = $1
        GROUP BY c.channel_id, c.system_kind, c.sensor_kind, c.name, c.is_active
        ORDER BY faults_cnt DESC, last_fault_at DESC NULLS LAST, avg_duration_h DESC NULLS LAST,
                 c.channel_id
        LIMIT $2 OFFSET $3
        """,
        section_id, limit, offset,
    )
    return {"total": total, "items": [dict(r) for r in rows]}


def _selfcheck():
    """Порядок маршрутов: GET /api/objects/tree обязан попасть в get_tree, а не в
    /objects/{section_id}. Переставь их — «tree» уйдёт в целочисленный section_id,
    и стенд ответит 422 (так было до MOS-101). Без стенда и без базы: только
    сопоставление маршрутов Starlette."""
    from starlette.routing import Match

    # Порядок, который решает, — внутри этого router: оба пути объявлены здесь.
    scope = {"type": "http", "path": "/api/objects/tree", "method": "GET"}
    первый = next(r for r in router.routes if r.matches(scope)[0] == Match.FULL)
    assert первый.path == "/api/objects/tree", (
        f"/api/objects/tree достался маршруту {первый.path} — /objects/tree должен стоять выше"
    )
    print("objects selfcheck ok: /api/objects/tree → get_tree")


if __name__ == "__main__":
    _selfcheck()
