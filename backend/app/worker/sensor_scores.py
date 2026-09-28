"""Балл риска по каждому датчику в pred.sensor_risk. Эпик MOS-248, задача SL.3 (MOS-252).

Планировщик зовёт `посчитать()` сразу после прогноза (`scheduler.тик_расчёт`), на том
же срезе, что отдаёт GET /api/risks: max(as_of) в pred.forecast_current. Прогноза нет —
считать не на что, тик молчит.

Модель одна — `app.domain.sensor_risk` (SL.10, MOS-263; коэффициенты
backend/app/domain/sensor_model.json): отказы smvu.model_failure_event (как
у карточки участка, от нижней границы pred.weight_window()), синтетический паспорт
из db/seed/sensor_demo.sql, окна графика ППР из maint.ppr_window (MOS-251). Здесь
чтение входа четырьмя запросами, счёт отказов соседей за 7 сут и запись результата:
на 11,5 тыс. каналов это четыре выборки и один COPY.

Блокировка своя — `pg_try_advisory_lock(48219)`, рядом с 48217 расчёта (run.py)
и 48218 самопроверки планировщика: занято — тик выходит сразу, без ожидания.
Строки среза пишутся в одной транзакции «удалить срез — вставить срез», так что
метод никогда не видит половину парка.

Запуск руками:
    python -m app.worker.sensor_scores           # посчитать на текущем срезе, нужна база
"""

import asyncio
import json
import os
import time
from collections import Counter
from datetime import timedelta

from app.domain import sensor_risk

БЛОКИРОВКА = 48219
# Сколько срезов держим: сутки до текущего. Всё новее текущего удаляем — при
# перезапуске проигрывания архива срез уходит назад, и метод не должен читать
# «будущий» срез, оставшийся от прошлого прохода.
ХРАНИТЬ = timedelta(days=1)
НЕДЕЛЯ = timedelta(days=7)

КАНАЛЫ = """
SELECT c.channel_id, c.object_id, c.sensor_kind, c.collector, c.picket,
       e.id AS eq_id, e.in_service_from, e.service_life_years
  FROM smvu.channel c
  LEFT JOIN asset.equipment e ON e.id = c.equipment_id AND e.source_system = $1
 WHERE c.is_active AND NOT c.is_stub
"""
# Последняя проверка каждой точки измерения не позже даты среза: score() берёт
# из истории только её, поэтому всю историю не тащим.
ПРОВЕРКИ = """
SELECT p.equipment_id, ch.code,
       max(timezone('Europe/Moscow', ms.measured_at)::date)
           FILTER (WHERE timezone('Europe/Moscow', ms.measured_at)::date <= $2) AS last
  FROM asset.measuring_point p
  JOIN asset.equipment e ON e.id = p.equipment_id AND e.source_system = $1
  JOIN ref.characteristic ch ON ch.id = p.characteristic_id
  LEFT JOIN asset.measurement ms ON ms.point_id = p.id
 GROUP BY p.id, p.equipment_id, ch.code
"""
# Отказы всех каналов, а не только активных: соседей на пикете и коллекторе
# модель училась считать по всем отказам группы (docs/proof/2026-09-28-sensor-model).
ОТКАЗЫ = """
SELECT e.channel_id, e.started_at, c.object_id, c.sensor_kind, c.collector, c.picket
  FROM smvu.model_failure_event e
  JOIN smvu.channel c USING (channel_id)
 CROSS JOIN pred.weight_window() w
 WHERE timezone('Europe/Moscow', e.started_at)::date >= w.date_from
   AND e.started_at <= $1
"""


async def баллы(conn, at) -> list[tuple]:
    """Строки pred.sensor_risk на срез `at` — без записи, для тика и для проверок."""
    день = at.astimezone(sensor_risk.MSK).date()
    точки: dict[int, list] = {}
    for r in await conn.fetch(ПРОВЕРКИ, sensor_risk.SRC, день):
        точки.setdefault(r["equipment_id"], []).append(
            {
                "kind": "calib" if r["code"] == "SYN_CALIB_ERR" else "motohours",
                "readings": [(r["last"],)] if r["last"] else [],
            }
        )
    окна = sensor_risk.plan_windows(await conn.fetch(sensor_risk.ОКНА))
    starts: dict[int, list] = {}
    # Соседи: отказы за 7 сут до среза по коллектору и по паре (коллектор, пикет),
    # эпизоды в окнах ППР не считаются — так же, как у самого канала.
    свои: Counter = Counter()
    по_коллектору: Counter = Counter()
    по_пикету: Counter = Counter()
    for r in await conn.fetch(ОТКАЗЫ, at):
        s = r["started_at"]
        starts.setdefault(r["channel_id"], []).append(s)
        if not timedelta(0) <= at - s < НЕДЕЛЯ or sensor_risk.in_plan(
            s, окна.get((r["object_id"], r["sensor_kind"]), ())
        ):
            continue
        свои[r["channel_id"]] += 1
        if r["collector"] is not None:
            по_коллектору[r["collector"]] += 1
            if r["picket"] is not None:
                по_пикету[(r["collector"], r["picket"])] += 1
    строки = []
    for c in await conn.fetch(КАНАЛЫ, sensor_risk.SRC):
        eq = c["eq_id"] and {
            "in_service": c["in_service_from"],
            "life": c["service_life_years"],
            "points": точки.get(c["eq_id"], []),
        }
        n = свои[c["channel_id"]]
        col, pk = c["collector"], c["picket"]
        nb = (
            по_пикету[(col, pk)] - n if col is not None and pk is not None else 0,
            по_коллектору[col] - n if col is not None else 0,
        )
        b = sensor_risk.split(
            starts.get(c["channel_id"], []),
            eq or None,
            at,
            окна.get((c["object_id"], c["sensor_kind"]), ()),
            nb,
            c["sensor_kind"],
        )
        строки.append(
            (
                c["channel_id"],
                at,
                b["score_real"],
                b["score_synth"],
                b["level_real"],
                b["level_full"],
                json.dumps(b["reasons"], ensure_ascii=False),
                json.dumps(b["reasons_real"], ensure_ascii=False),
            )
        )
    return строки


async def посчитать(conn) -> dict:
    """Посчитать и записать срез. Отдаёт {"status": …, "as_of", "rows", "ms"}."""
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА):
        return {"status": "занято"}
    try:
        at = await conn.fetchval("SELECT max(as_of) FROM pred.forecast_current")
        if at is None:
            return {"status": "нет прогноза"}
        t0 = time.monotonic()
        строки = await баллы(conn, at)
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM pred.sensor_risk WHERE as_of >= $1 OR as_of < $2",
                at,
                at - ХРАНИТЬ,
            )
            await conn.copy_records_to_table(
                "sensor_risk",
                schema_name="pred",
                records=строки,
                columns=[
                    "channel_id",
                    "as_of",
                    "score_real",
                    "score_synth",
                    "level_real",
                    "level_full",
                    "reasons",
                    "reasons_real",
                ],
            )
        return {
            "status": "ok",
            "as_of": at,
            "rows": len(строки),
            "ms": round((time.monotonic() - t0) * 1000),
        }
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА)


async def _main():
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=300)
    try:
        print(await посчитать(conn))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
