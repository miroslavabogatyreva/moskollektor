"""Синтетическая геометрия коллекторов и участков. Задача MOS-45 (Q4.8), приёмка Ф-81.

Координат в выгрузке нет, и заказчик 17.09.2026 разрешил нарисовать их самим:
«Достаточно осевых MultiLineString». Рисуем так:

* Коллектор — MULTILINESTRING, по одной части на префикс тега внутри коллектора:
  пара (коллектор, префикс) — это та же линия оси, что AxisLine.tsx рисует на схеме.
  Пар 32 на 16 коллекторов: префиксы 798 и 163 делят по два коллектора, и у каждого
  своя часть. Коллектор участка — УЧАСТКИ_КОЛЛЕКТОРА из расчёта, второй копии правила нет.
* Часть — отрезок на север длиной (последний пикет + 1) × 10 м. Все части начинаются
  от одной точки в центре Москвы и отодвинуты друг от друга на восток на ШАГ_М, чтобы
  не лечь одна на другую. Концы — ST_Project по geography, то есть точная геодезия:
  градус долготы на широте Москвы вдвое короче экваторных 111 320 м.
* Участок — LINESTRING, отрезок [пикет × 10, пикет × 10 + 10] м своей части через
  ST_LineSubstring. Метры из пикета даёт linear_metres() из tag_to_section.py.

Повторный запуск ничего не удваивает: geo.geo_object держит уникальный индекс
по smvu_id («collector:<id>» у коллектора, smvu_key у участка), запись идёт через
ON CONFLICT … DO UPDATE. Вызывает повторный проход --channels (smvu_csv.load_channels).
"""

from collections import defaultdict

from app.worker.run_v3 import УЧАСТКИ_КОЛЛЕКТОРА

from .tag_to_section import linear_metres

НАЧАЛО = (37.6173, 55.7558)  # долгота, широта: центр Москвы, условная точка отсчёта
ШАГ_М = 200.0  # между соседними частями на восток
ДЛИНА_ПИКЕТА_М = 10.0

УЧАСТКИ = f"""
WITH u AS ({УЧАСТКИ_КОЛЛЕКТОРА})
SELECT u.section_id, u.collector_id, x.smvu_key, t.name AS collector_name
  FROM u
  JOIN ref.object_xref x ON x.section_id = u.section_id
  JOIN smvu.object_tree t ON t.object_id = u.collector_id
"""

# $1…$4 — части, $5…$9 — участки, $10/$11 — точка отсчёта, $12 — шаг.
НАРИСОВАТЬ = """
WITH часть AS (
    SELECT p.collector_id, p.pfx, p.idx, p.len_m,
           ST_MakeLine(s::geometry, ST_Project(s, p.len_m, 0)::geometry) AS geom
      FROM unnest($1::int[], $2::text[], $3::int[], $4::float8[]) AS p(collector_id, pfx, idx, len_m)
     CROSS JOIN LATERAL (
        SELECT ST_Project(ST_SetSRID(ST_MakePoint($10, $11), 4326)::geography,
                          $12 * p.idx, pi() / 2) AS s) o
), коллектор AS (
    INSERT INTO geo.geo_object (kind_code, name_full, name_short, smvu_id, source_name, geom)
    SELECT 'collector', max(t.name), max(t.name), 'collector:' || ч.collector_id, 'synthetic',
           ST_Multi(ST_Collect(ч.geom ORDER BY ч.pfx))
      FROM часть ч JOIN smvu.object_tree t ON t.object_id = ч.collector_id
     GROUP BY ч.collector_id
    ON CONFLICT (smvu_id) WHERE smvu_id IS NOT NULL
    DO UPDATE SET geom = excluded.geom, name_full = excluded.name_full, name_short = excluded.name_short
    RETURNING 1
), участок AS (
    INSERT INTO geo.geo_object (kind_code, name_full, name_short, smvu_id, source_name, geom)
    SELECT 'collector_section', 'Участок ' || s.smvu_key, s.smvu_key, s.smvu_key, 'synthetic',
           ST_LineSubstring(ч.geom, s.from_m / ч.len_m, s.to_m / ч.len_m)
      FROM unnest($5::text[], $6::int[], $7::text[], $8::float8[], $9::float8[])
               AS s(smvu_key, collector_id, pfx, from_m, to_m)
      JOIN часть ч ON ч.collector_id = s.collector_id AND ч.pfx = s.pfx
    ON CONFLICT (smvu_id) WHERE smvu_id IS NOT NULL
    DO UPDATE SET geom = excluded.geom
    RETURNING object_id, smvu_id
), связь AS (
    UPDATE ref.object_xref x SET geo_object_id = у.object_id
      FROM участок у
     WHERE x.smvu_key = у.smvu_id AND x.geo_object_id IS DISTINCT FROM у.object_id
    RETURNING 1
)
SELECT (SELECT count(*) FROM коллектор) AS коллекторов,
       (SELECT count(*) FROM участок) AS участков,
       (SELECT count(*) FROM связь) AS связей
"""


def разложить(rows):
    """Строки участков -> (части, участки) в виде колонок для unnest.

    Части — пары (коллектор, префикс) в порядке сортировки, idx — номер части
    по порядку, он же сдвиг на восток. Длина части — конец последнего участка.
    """
    по_части = defaultdict(list)
    for r in rows:
        pfx, pk = r["smvu_key"].split(":")
        начало = linear_metres(f"ПК{pk}")
        по_части[(r["collector_id"], pfx)].append((r["smvu_key"], начало))
    части = [[], [], [], []]
    участки = [[], [], [], [], []]
    for idx, ключ in enumerate(sorted(по_части)):
        collector_id, pfx = ключ
        длина = max(н for _, н in по_части[ключ]) + ДЛИНА_ПИКЕТА_М
        for колонка, v in zip(части, (collector_id, pfx, idx, длина)):
            колонка.append(v)
        for smvu_key, н in по_части[ключ]:
            for колонка, v in zip(
                участки, (smvu_key, collector_id, pfx, н, н + ДЛИНА_ПИКЕТА_М)
            ):
                колонка.append(v)
    return части, участки


async def нарисовать_геометрию(conn):
    """Рисует оси коллекторов и отрезки участков. Без вида collector (046) — пропуск."""
    if not await conn.fetchval(
        "SELECT true FROM geo.object_kind WHERE code = 'collector'"
    ):
        print("геометрия: вида collector нет (миграция 046 не накатана) — пропускаю")
        return
    rows = await conn.fetch(УЧАСТКИ)
    if not rows:
        print(
            "геометрия: участков с коллектором нет (справочник каналов пуст) — пропускаю"
        )
        return
    части, участки = разложить(rows)
    итог = await conn.fetchrow(НАРИСОВАТЬ, *части, *участки, *НАЧАЛО, ШАГ_М)
    print(
        f"геометрия: коллекторов {итог['коллекторов']}, частей {len(части[0])}, "
        f"участков {итог['участков']}, новых связей с ref.object_xref {итог['связей']}"
    )


def _selfcheck():
    rows = [
        {"collector_id": 12, "smvu_key": "798:0"},
        {"collector_id": 12, "smvu_key": "798:3"},
        {"collector_id": 6, "smvu_key": "798:5"},
        {"collector_id": 6, "smvu_key": "15:176"},
    ]
    части, участки = разложить(rows)
    # Префикс 798 у двух коллекторов — две части, а не одна.
    assert list(zip(части[0], части[1])) == [(6, "15"), (6, "798"), (12, "798")], части
    assert части[2] == [0, 1, 2]
    # Длина части — конец последнего участка: пикет 176 -> 1 760 + 10 м.
    assert части[3] == [1770.0, 60.0, 40.0], части[3]
    i = участки[0].index("798:3")
    assert (участки[3][i], участки[4][i]) == (30.0, 40.0)
    assert all(b - a == ДЛИНА_ПИКЕТА_М for a, b in zip(участки[3], участки[4]))
    print("selfcheck ok")


async def _нарисовать(dsn):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await нарисовать_геометрию(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    # Без аргументов — самопроверка. С --dsn — нарисовать на уже работающем стенде,
    # не перезаливая справочник (deploy/README.md, «на уже работающем стенде»).
    import argparse
    import asyncio
    import time

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", help="postgresql://… ; без него только самопроверка")
    args = ap.parse_args()
    if not args.dsn:
        _selfcheck()
    else:
        t = time.monotonic()
        asyncio.run(_нарисовать(args.dsn))
        print(f"геометрия: {time.monotonic() - t:.1f} с")
