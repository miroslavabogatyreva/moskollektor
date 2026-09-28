#!/usr/bin/env python3
"""Самопроверка синтетического паспорта оборудования db/seed/sensor_demo.sql (MOS-250).

Реестра оборудования, поверок и моточасов заказчик не дал (ОВ-08, ОВ-14 закрыты
17.09.2026 отказом), поэтому паспорт каждого активного канала ВЫДУМАН и помечен
source_system='synthetic-demo'. До 28.09.2026 этот скрипт генерировал SQL на Python
для 188 каналов узла 5657 (сид весил 400 КБ); теперь паспорт считает сама база
от hashtext(channel_id) на весь парк, а скрипт только проверяет сид.

Главное, что он доказывает: синтетика НЕ выведена из реальных отказов. Подгони мы
год выпуска или просрочку поверки под отказы — балл «угадывал» бы ответ, который
сам же и подсмотрел. Доказательство двумя способами:
  * --selfcheck, без базы: текст сида не упоминает ни одной таблицы отказов,
    показаний и прогнозов, а метка версии совпадает с хешем тела;
  * --db DSN (или check_on_db() из backend/tests/test_sensor_risk_db.py): в
    транзакции с откатом паспорт снимается, каналам дописываются отказы, метка
    сбрасывается, сид накатывается заново — паспорт обязан совпасть до байта.
    Тем же прогоном: повторный накат ничего не трогает, каждый активный канал
    получил оборудование.

    python3 code/synth_sensor_level.py --selfcheck
    python3 code/synth_sensor_level.py --fix-mark     # после правки сида
    python3 code/synth_sensor_level.py --db postgresql://…   # только локальная база
"""

import argparse
import asyncio
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_SQL = ROOT / "db/seed/sensor_demo.sql"
SRC = "synthetic-demo"
MARK_RE = re.compile(
    r"(mark constant text := 'Оборудование СМВУ, synthetic-demo )([0-9a-f]{12})(';)"
)
# Что сид читать не вправе: отказы, показания, эпизоды, прогнозы.
FORBIDDEN = ("model_failure", "fault_", "smvu.reading", "feat.", "pred.", "ods_event")

# Паспорт канала так, как его видят балл и экран, — для сравнения до и после.
PASSPORT = f"""
SELECT c.channel_id, e.equipment_no, e.name, k.code, e.in_service_from, e.purchase_date,
       e.purchase_value, m.name AS maker, e.model_no, e.serial_no, e.service_life_years,
       (SELECT json_agg(json_build_object('at', ms.measured_at, 'v', ms.value_num,
                                          'd', ms.delta_num, 'oob', ms.is_out_of_limit)
                        ORDER BY ms.measured_at)
          FROM asset.measuring_point p JOIN asset.measurement ms ON ms.point_id = p.id
         WHERE p.equipment_id = e.id)::text AS history
  FROM smvu.channel c
  JOIN asset.equipment e ON e.id = c.equipment_id AND e.source_system = '{SRC}'
  JOIN ref.object_kind k ON k.id = e.object_kind_id
  LEFT JOIN ref.manufacturer m ON m.id = e.manufacturer_id
 ORDER BY c.channel_id
"""
IDS = f"SELECT array_agg(id ORDER BY id) FROM asset.equipment WHERE source_system = '{SRC}'"


def body_hash(text):
    body = MARK_RE.sub(r"\g<1>000000000000\g<3>", text)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def selfcheck():
    text = SEED_SQL.read_text()
    m = MARK_RE.search(text)
    assert m, "в сиде нет метки версии"
    assert m.group(2) == body_hash(text), (
        f"метка {m.group(2)} отстала от тела ({body_hash(text)}): "
        "python3 code/synth_sensor_level.py --fix-mark"
    )
    code = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    leaks = [w for w in FORBIDDEN if w in code]
    assert not leaks, f"сид читает запрещённое: {leaks}"
    assert "random(" not in code, "random() недетерминирован, только hashtext"
    assert code.count("$synth$") == 2 and "COMMIT;" not in code and "BEGIN;" not in code
    assert len(text) < 20_000, f"сид разросся до {len(text)} байт"
    print(f"selfcheck ok: метка {m.group(2)}, {len(text)} байт, таблиц отказов в сиде нет")


def fix_mark():
    text = SEED_SQL.read_text()
    SEED_SQL.write_text(MARK_RE.sub(rf"\g<1>{body_hash(text)}\g<3>", text))
    print(f"метка: {body_hash(text)}")


async def check_on_db(conn):
    """Прогон на базе с каналами, в транзакции с откатом. Отдаёт число паспортов."""
    seed = SEED_SQL.read_text()
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(seed)
        active = await conn.fetchval(
            "SELECT count(*) FROM smvu.channel WHERE is_active AND NOT is_stub"
        )
        before = [tuple(r) for r in await conn.fetch(PASSPORT)]
        assert active and len(before) == active, (len(before), active)
        ids = await conn.fetchval(IDS)
        # повторный накат при той же метке ничего не трогает
        await conn.execute(seed)
        assert ids == await conn.fetchval(IDS), "повторный накат переписал паспорт"
        # утечки нет: отказы у каждого третьего канала, метка сброшена — паспорт тот же
        await conn.execute("""
            INSERT INTO smvu.model_failure_episode
                   (channel_id, section_id, started_at, ended_at, fault_value, model_version)
            SELECT c.channel_id, c.section_id, s.t, s.t + interval '5 hours',
                   v.fault_value, v.model_version
              FROM smvu.channel c
             CROSS JOIN (SELECT * FROM smvu.model_failure_value LIMIT 1) v
             CROSS JOIN (VALUES (timestamptz '2026-06-20 10:00+03'),
                                (timestamptz '2026-05-01 10:00+03')) AS s(t)
             WHERE c.is_active AND NOT c.is_stub AND c.channel_id % 3 = 0""")
        await conn.execute("UPDATE ref.equipment_type SET name = 'сброшено' WHERE code = 'S'")
        await conn.execute(seed)
        after = [tuple(r) for r in await conn.fetch(PASSPORT)]
        assert before == after, "паспорт изменился от отказов — синтетика подсмотрела ответ"
        assert ids != await conn.fetchval(IDS), (
            "сброс метки не пересоздал паспорт — проверка выше ничего не доказала"
        )
        return len(after)
    finally:
        await tr.rollback()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--fix-mark", action="store_true")
    ap.add_argument("--db", help="DSN локальной базы с каналами; всё откатывается")
    a = ap.parse_args()
    if a.fix_mark:
        return fix_mark()
    if a.db:
        import asyncpg

        async def run():
            conn = await asyncpg.connect(a.db)
            try:
                n = await check_on_db(conn)
            finally:
                await conn.close()
            print(f"db ok: {n} паспортов, повтор не трогает, отказы паспорт не меняют")

        return asyncio.run(run())
    if not a.selfcheck:
        sys.exit("нужен --selfcheck, --fix-mark или --db")
    selfcheck()


if __name__ == "__main__":
    main()
