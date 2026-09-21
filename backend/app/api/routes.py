"""Списочные методы API: риски и прогнозы. Задача MOS-40 (Q4.3), приёмка М-15, М-16.

Пустой результат — 200 и пустой список (Готовность блока Q4, docs/plan.md):
расчёт пишет соседняя сессия, до первого прогона строк в pred.forecast
и pred.forecast_current нет вовсе, и это не повод отвечать ошибкой.
"""
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/risks")
async def list_risks(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("risks.read")),
):
    """Текущий риск по каждому участку — одна строка на участок (М-15).

    `as_of` — срез данных: момент, на который посчитаны признаки. Это НЕ время
    расчёта. До 21.09.2026 поле называлось здесь `computed_at`, а в GET /api/forecasts
    тем же именем ехало время работы расчёта — одно имя, две разные величины,
    и клиент, читавший оба метода, сравнивал несравнимое (MOS-118). Теперь
    `as_of` везде означает срез данных, `computed_at` — время расчёта.

    Пока расчёт брал срез по текущей дате, разница была микросекундной и увидеть
    её было нельзя; после MOS-142 срез идёт по краю выгрузки (30.06.2026), и две
    даты расходятся на 83 суток.
    """
    rows = await conn.fetch(
        """
        SELECT section_id, probability, risk_rank, horizon_h, as_of, is_stale
        FROM pred.forecast_current
        ORDER BY risk_rank
        """
    )
    return [dict(r) for r in rows]


@router.get("/forecasts")
async def list_forecasts(
    from_: date | None = Query(None, alias="from", description="дата начала периода, включительно"),
    to: date | None = Query(None, description="дата конца периода, включительно — весь день целиком"),
    limit: int = Query(200, ge=1, le=1000, description="сколько записей вернуть, потолок 1000"),
    offset: int = Query(0, ge=0, description="сколько записей пропустить от начала выборки"),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("forecasts.read")),
):
    """Прогнозы за период (М-16). Две даты в каждой строке, и они значат разное.

    `as_of` — срез данных, на котором считали (`pred.run.as_of`); `computed_at` —
    время работы расчёта (`pred.run.started_at`). Раньше строка несла только вторую
    дату, а GET /api/risks отдавал под этим же именем первую — MOS-118. Обе даты
    рядом стоят дешевле одной: `pred.run` и так в соединении, лишнего чтения нет,
    а читатель ответа видит, чем они отличаются, не заглядывая в документ.

    **ПРОПУСК В ЖУРНАЛЕ ОЗНАЧАЕТ «ЗНАЧЕНИЕ НЕ МЕНЯЛОСЬ», А НЕ «РАСЧЁТ НЕ ШЁЛ»**
    (MOS-147). С миграции 026 расчёт пишет строку, только когда вероятность вышла
    за мёртвую зону, сменился класс риска или подошёл безусловный «пульс» — раз
    в час на объект. Между двумя строками объекта расчёт по нему шёл, и результат
    был прежним. Отличить одно от другого позволяет `write_reason` в каждой строке:
    `first` — первый прогноз объекта, `change` — изменение, `heartbeat` — пульс,
    `full` — прогон с полным журналом (обратный расчёт для замера метрик).
    Пропуски самого расчёта видны не здесь, а в `pred.run` — их стережёт строка
    приёмки НФ-91 в `code/check_runtime.py`.

    from/to — даты, а не моменты времени, и обе границы включительны. Раньше
    to сравнивался как timestamptz <= 'ГГГГ-ММ-ДД 00:00' и вырезал весь день,
    который назвали: запрос «сегодня с сегодня» при полной базе отвечал пустым
    списком — нашла фронт-сессия 16.09.2026 на боевом контуре. Здесь верхняя
    граница — начало СЛЕДУЮЩЕГО дня, сравнение строгое: включает весь to целиком.

    limit/offset — М-06: без них метод отдаёт журнал целиком (425 183 строки,
    78 МБ на 21.09.2026 на боевом стенде) — страница браузера с этим не
    справляется. total в ответе — число совпадений по фильтру без обрезки
    страницей, а не длина items. **Два запроса, а не count(*) OVER() в одном:**
    на полном журнале без фильтра по дате `pred.forecast` идёт последовательным
    сканированием (индекса на `run_id`/`started_at` под эту сортировку нет),
    и оконная функция считает total до обрезки страницей — заставляет
    материализовать и отсортировать все 425 183 строки вместо top-N по LIMIT.
    Замер 21.09.2026 на боевом стенде: один запрос с count(*) OVER() — 415 мс;
    отдельные COUNT и LIMIT-запрос — 68 мс + 121 мс = 189 мс, больше чем
    вдвое быстрее. WHERE не дублирован текстом — это одна переменная `where`,
    вставленная в оба запроса f-строкой, чтобы условие не могло разойтись
    между двумя местами так же, как разошлась когда-то граница `to`.
    """
    to_exclusive = to + timedelta(days=1) if to else None
    where = """
        WHERE ($1::date IS NULL OR r.started_at >= $1)
          AND ($2::timestamptz IS NULL OR r.started_at < $2)
    """
    total = await conn.fetchval(
        f"""
        SELECT count(*)
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        {where}
        """,
        from_, to_exclusive,
    )
    rows = await conn.fetch(
        f"""
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, r.as_of, r.started_at AS computed_at,
               f.write_reason
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        {where}
        ORDER BY r.started_at DESC, f.risk_rank
        LIMIT $3 OFFSET $4
        """,
        from_, to_exclusive, limit, offset,
    )
    return {
        "total": total,
        "items": [
            {
                "forecast_id": r["forecast_id"],
                "section_id": r["section_id"],
                "direction": r["direction"],
                "horizon_h": r["horizon_h"],
                "probability": r["probability"],
                "risk_rank": r["risk_rank"],
                "as_of": r["as_of"],
                "computed_at": r["computed_at"],
                "write_reason": r["write_reason"],
            }
            for r in rows
        ],
    }


@router.get("/forecasts/{forecast_id}")
async def get_forecast(
    forecast_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("forecasts.read")),
):
    row = await conn.fetchrow(
        """
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, f.factors,
               -- Два разных момента: as_of — срез данных, на котором считали
               -- (снимок выгрузки заказчика); computed_at (r.started_at) —
               -- когда сам расчёт выполнился. Разводит их та же подпись, что
               -- в ObjectCard.current_risk (оркестратор, 17.09.2026).
               r.as_of, r.started_at AS computed_at,
               -- М-12: заявки, которых породил этот прогноз (Q6.5, MOS-60).
               -- Массив, не null: заявок может не быть, метода — не бывает.
               COALESCE(
                   (SELECT array_agg(n.id ORDER BY n.id)
                      FROM maint.notification n
                     WHERE n.forecast_id = f.forecast_id),
                   ARRAY[]::bigint[]
               ) AS order_ids
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE f.forecast_id = $1
        """,
        forecast_id,
    )
    if row is None:
        raise HTTPException(404, "прогноз не найден")
    return dict(row)
