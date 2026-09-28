#!/usr/bin/env python3
"""Синтетический паспорт оборудования для «объект Каппа ДУ» и балл риска по датчику.

Демо «как система работает на уровне датчика, если заказчик даст реестр и ремонты».
Реестра оборудования, поверок и моточасов заказчик не дал (ОВ-08, ОВ-14 закрыты
17.09.2026 отказом), поэтому всё в asset.* здесь ВЫДУМАНО и помечено
source_system='synthetic-demo'. Настоящее только два поля: каналы smvu.channel
и отказы smvu.model_failure_event (как у карточки участка).

Балл считает backend/app/domain/sensor_risk.py — тот же, что отдаёт GET /api/sensor-risk;
здесь своей формулы нет. SQL синтетики пишется сидом db/seed/sensor_demo.sql: его накатывает
контейнер migrate на каждом прогоне, и сид сам молчит, пока каналов Каппы ДУ в базе нет.

Синтетика строится из channel_id и SEED и НЕ видит отказов: подгони мы год выпуска
или просрочку поверки под реальные отказы — балл «угадывал» бы ответ, который сам
же и подсмотрел. selfcheck это проверяет.

Входные данные — один SELECT на стенде (только чтение), текст запроса: --query.
    python3 code/synth_sensor_level.py --query | ssh root@… 'docker exec -i moskollektor-db-1 \\
        psql -U moskollektor -d moskollektor -tA' > input.json
    python3 code/synth_sensor_level.py input.json [--at 2026-06-22T21:00:00+03:00]
    python3 code/synth_sensor_level.py --selfcheck
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.domain.sensor_risk import INTERVAL, SRC, plan_windows, score

SEED = 20260928
NODE = 5657  # smvu.object_tree «объект Каппа ДУ», родитель — коллектор «объект Каппа» (15)
# Дата, от которой отсчитана синтетика (конец архива СМВУ). Константа, а не --at:
# паспорт оборудования не должен меняться от того, на какой момент считаем балл.
REF_DATE = date(2026, 6, 30)
DEFAULT_AT = "2026-06-30T23:59:59+03:00"
PROOF = ROOT / "docs/proof/2026-09-28-sensor-level"
SEED_SQL = ROOT / "db/seed/sensor_demo.sql"

# Отказы — как у карточки (GET /api/objects/{id}/channels): smvu.model_failure_event
# от нижней границы pred.weight_window().
QUERY = f"""WITH ch AS (
  SELECT c.channel_id, c.name, c.picket, c.sensor_kind, c.section_id
    FROM smvu.channel c WHERE c.object_id = {NODE} AND c.is_active)
SELECT json_build_object(
  'channels', (SELECT json_agg(ch ORDER BY channel_id) FROM ch),
  'episodes', (SELECT json_agg(json_build_object('channel_id', e.channel_id,
                 'started_at', e.started_at, 'ended_at', e.ended_at)
                 ORDER BY e.channel_id, e.started_at)
                 FROM smvu.model_failure_event e JOIN ch USING (channel_id)
                CROSS JOIN pred.weight_window() w
                WHERE timezone('Europe/Moscow', e.started_at)::date >= w.date_from));"""

# sensor_kind -> (код вида объекта, название вида, критичность, срок службы лет,
#                  производители, что меряем: None | 'calib' | 'motohours')
KINDS = {
    "Газовый датчик": (
        "DEGD",
        "Газоанализатор стационарный",
        "A",
        10,
        ["ООО «Газсенсор-Демо»", "АО «Аналит-Демо»"],
        "calib",
    ),
    "Состояние насоса": (
        "PUCE",
        "Насос дренажный центробежный",
        "B",
        10,
        ["ООО «Дренаж-Демо»", "АО «Насосмаш-Демо»"],
        "motohours",
    ),
    "Состояние вентилятора": (
        "ATFA",
        "Вентилятор приточно-вытяжной",
        "B",
        15,
        ["АО «Вентмаш-Демо»", "ООО «Аэро-Демо»"],
        "motohours",
    ),
    "Состояние фазы": (
        "SWPH",
        "Реле контроля фаз",
        "C",
        12,
        ["ООО «Релейка-Демо»", "АО «Электроавтоматика-Демо»"],
        None,
    ),
    "Переключатель": (
        "SWMS",
        "Переключатель режима",
        "C",
        15,
        ["ООО «Релейка-Демо»", "АО «Электроавтоматика-Демо»"],
        None,
    ),
    "ИБП": (
        "ELUP",
        "Источник бесперебойного питания",
        "B",
        8,
        ["ООО «Энергорезерв-Демо»"],
        None,
    ),
}
OTHER = ("SWXX", "Датчик прочий", "C", 12, ["ООО «Релейка-Демо»"], None)
HOURS_PER_YEAR = {"PUCE": 1500, "ATFA": 4000}  # насос дренажный работает урывками
CALIB_LIMIT = 10.0  # допустимая основная погрешность, %

def passport(ch, seed=SEED):
    """Синтетический паспорт одного канала. Отказов на вход НЕ получает."""
    r = random.Random(f"{seed}:{ch['channel_id']}")
    code, kind_name, crit, life, makers, meas = KINDS.get(ch["sensor_kind"], OTHER)
    build = date(r.randint(2008, 2023), r.randint(1, 12), 1)
    in_service = min(build + timedelta(days=r.randint(30, 365)), REF_DATE)
    eq = {
        "channel_id": ch["channel_id"],
        "section_id": ch["section_id"],
        "equipment_no": str(900_000_000 + ch["channel_id"]),
        "name": f"{kind_name} {ch['name']}",
        "kind": code,
        "crit": crit,
        "maker": r.choice(makers),
        "build": build,
        "in_service": in_service,
        "life": life,
        "serial": f"SD-{r.randrange(10**7):07d}",
        "model": f"{code}-{r.choice(['100', '200', '300'])}",
        "price": round(r.uniform(8, 60 if code in HOURS_PER_YEAR else 25) * 1000, -2),
        "points": [],
    }
    if meas:
        interval = INTERVAL[meas]
        # последняя проверка от 10 до 2 интервалов назад: треть просрочена
        last = REF_DATE - timedelta(days=r.randint(10, 2 * interval))
        # Датчики, снятые на поверку по настоящему графику ППР заказчика, поверены
        # в день вывоза из ОМ — иначе экран писал бы рядом «плановый демонтаж
        # на поверку» и «поверка просрочена».
        for w in plan_windows(NODE, ch["sensor_kind"]):
            last = w["to"].date()
        dates, d = [], last
        while d >= in_service:
            dates.append(d)
            d -= timedelta(days=interval + r.randint(-20, 20))
        dates.reverse()
        if meas == "calib":
            vals = [round(r.uniform(0.5, 11.0), 2) for _ in dates]  # погрешность, %
        else:
            vals, acc, prev = [], 0.0, in_service
            for d in dates:
                acc += (
                    (d - prev).days / 365 * HOURS_PER_YEAR[code] * r.uniform(0.6, 1.3)
                )
                vals.append(round(acc))
                prev = d
        eq["points"].append(
            {
                "kind": meas,
                "point_no": 9_000_000_000 + ch["channel_id"],
                "readings": list(zip(dates, vals)),
            }
        )
    return eq


def q(v):
    """Литерал SQL."""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def to_sql(eqs):
    eq_id = "(SELECT id FROM asset.equipment WHERE equipment_no = {})".format
    out = [
        f"UPDATE smvu.channel SET equipment_id = NULL WHERE equipment_id IN "
        f"(SELECT id FROM asset.equipment WHERE source_system = '{SRC}');",
        f"DELETE FROM asset.measurement WHERE source_system = '{SRC}';",
        f"DELETE FROM asset.measuring_point WHERE equipment_id IN "
        f"(SELECT id FROM asset.equipment WHERE source_system = '{SRC}');",
        # created_by — внешний ключ на app_user (001_assets.sql, eq_install_hist_created_by_fk):
        # метку туда писать нельзя, чистим по синтетическому оборудованию.
        f"DELETE FROM asset.equipment_install_history WHERE equipment_id IN "
        f"(SELECT id FROM asset.equipment WHERE source_system = '{SRC}');",
        f"DELETE FROM asset.equipment WHERE source_system = '{SRC}';",
        "",
        "INSERT INTO ref.equipment_type (code, name, number_range_from, number_range_to) VALUES "
        f"('S', 'Оборудование СМВУ, {SRC}', 900000000, 999999999) ON CONFLICT (code) DO NOTHING;",
    ]
    kinds = {k[0]: k[1] for k in [*KINDS.values(), OTHER]}
    out.append(
        "INSERT INTO ref.object_kind (code, family_code, name_ru, name_en) VALUES\n  "
        + ",\n  ".join(
            f"({q(c)}, {q(c[:2])}, {q(n)}, {q(SRC)})" for c, n in kinds.items()
        )
        + "\nON CONFLICT (code) DO NOTHING;"
    )
    makers = sorted({m for k in [*KINDS.values(), OTHER] for m in k[4]})
    out.append(
        "INSERT INTO ref.manufacturer (name) VALUES\n  "
        + ",\n  ".join(f"({q(m)})" for m in makers)
        + "\nON CONFLICT (name) DO NOTHING;"
    )
    out.append(
        "INSERT INTO ref.characteristic (code, name, data_type, uom, decimals) VALUES\n"
        f"  ('SYN_CALIB_ERR', 'Основная погрешность при поверке, {SRC}', 'num', '%', 2),\n"
        f"  ('SYN_MOTOHOURS', 'Наработка, моточасы, {SRC}', 'num', 'ч', 0)\n"
        "ON CONFLICT (code) DO NOTHING;\n"
    )
    for e in eqs:
        fl = (
            "(SELECT func_location_id FROM ref.object_xref WHERE section_id = "
            f"{e['section_id']})"
            if e["section_id"]
            else "NULL"
        )
        out.append(
            "INSERT INTO asset.equipment (equipment_no, name, equipment_type_id, object_kind_id, "
            "func_location_id, district_id, criticality_id, valid_from, in_service_from, "
            "purchase_date, purchase_value, manufacturer_id, manufacturer_country, model_no, "
            "serial_no, build_year, build_month, service_life_years, system_status, "
            "source_system, source_key) VALUES ("
            f"{q(e['equipment_no'])}, {q(e['name'])}, "
            "(SELECT id FROM ref.equipment_type WHERE code = 'S'), "
            f"(SELECT id FROM ref.object_kind WHERE code = {q(e['kind'])}), {fl}, "
            "(SELECT id FROM ref.district WHERE code = '01'), "
            f"(SELECT id FROM ref.criticality WHERE code = {q(e['crit'])}), "
            f"{q(e['in_service'])}, {q(e['in_service'])}, {q(e['build'])}, {e['price']}, "
            f"(SELECT id FROM ref.manufacturer WHERE name = {q(e['maker'])}), 'RU', "
            f"{q(e['model'])}, {q(e['serial'])}, {e['build'].year}, {e['build'].month}, "
            f"{e['life']}, 'INSTALLED', {q(SRC)}, {q(e['channel_id'])});"
        )
        out.append(
            "INSERT INTO asset.equipment_install_history (equipment_id, func_location_id, "
            f"installed_at, reason, created_by) VALUES ({eq_id(q(e['equipment_no']))}, {fl}, "
            f"{q(e['in_service'])}, 'Первичный монтаж ({SRC})', NULL);"
        )
        for p in e["points"]:
            calib = p["kind"] == "calib"
            out.append(
                "INSERT INTO asset.measuring_point (point_no, name, equipment_id, "
                "characteristic_id, is_counter, annual_estimate, upper_limit) VALUES ("
                f"{p['point_no']}, {q('Поверка газоанализатора' if calib else 'Наработка')}, "
                f"{eq_id(q(e['equipment_no']))}, (SELECT id FROM ref.characteristic WHERE code = "
                f"{q('SYN_CALIB_ERR' if calib else 'SYN_MOTOHOURS')}), {'false' if calib else 'true'}, "
                f"{'NULL' if calib else HOURS_PER_YEAR[e['kind']]}, "
                f"{CALIB_LIMIT if calib else 'NULL'});"
            )
            if p["readings"]:
                rows, prev = [], 0
                for d, v in p["readings"]:
                    delta = "NULL" if calib else v - prev
                    oob = "true" if calib and v > CALIB_LIMIT else "false"
                    rows.append(
                        f"  ((SELECT id FROM asset.measuring_point WHERE point_no = "
                        f"{p['point_no']}), '{d} 10:00+03', {v}, {delta}, {oob}, "
                        f"{q(SRC)})"
                    )
                    prev = v
                out.append(
                    "INSERT INTO asset.measurement (point_id, measured_at, value_num, "
                    "delta_num, is_out_of_limit, source_system) VALUES\n"
                    + ",\n".join(rows)
                    + ";"
                )
        out.append(
            f"UPDATE smvu.channel SET equipment_id = {eq_id(q(e['equipment_no']))} "
            f"WHERE channel_id = {e['channel_id']};\n"
        )
    body = "\n".join(out)
    # Метка версии: хеш тела. Совпала и все каналы привязаны — сид ничего не трогает,
    # иначе сносит свою прошлую синтетику и пишет заново.
    mark = f"Оборудование СМВУ, {SRC} {hashlib.sha256(body.encode()).hexdigest()[:12]}"
    ids = ", ".join(str(e["channel_id"]) for e in eqs)
    return f"""-- СИНТЕТИКА ({SRC}): паспорта, поверки и моточасы выдуманы, реальны только
-- каналы smvu.channel. НЕ ПРАВИТЬ РУКАМИ — генерирует code/synth_sensor_level.py, seed {SEED}.
-- Сид: накатывается контейнером migrate на каждом прогоне (backend/app/migrate.py).
-- Каналов {NODE} ещё нет (чистая установка до заливки) — ничего не делает;
-- синтетика этой версии уже стоит — ничего не делает; иначе переписывает свою.
DO $synth$
BEGIN
IF NOT EXISTS (SELECT 1 FROM smvu.channel WHERE channel_id IN ({ids})) THEN
  RAISE NOTICE 'sensor_demo: каналов узла {NODE} нет, пропускаю';
  RETURN;
END IF;
IF EXISTS (SELECT 1 FROM ref.equipment_type WHERE code = 'S' AND name = {q(mark)})
   AND (SELECT count(*) FROM smvu.channel c JOIN asset.equipment e ON e.id = c.equipment_id
         WHERE e.source_system = '{SRC}')
     = (SELECT count(*) FROM smvu.channel WHERE channel_id IN ({ids})) THEN
  RETURN;
END IF;
{body}
UPDATE ref.equipment_type SET name = {q(mark)} WHERE code = 'S';
END
$synth$;
"""


def run(data, at, seed=SEED):
    eqs = [passport(ch, seed) for ch in data["channels"]]
    by_ch = {}
    for ep in data["episodes"] or []:
        by_ch.setdefault(ep["channel_id"], []).append(ep)
    scores = [
        {
            **{k: ch[k] for k in ("channel_id", "name", "picket", "sensor_kind")},
            **score(
                [
                    datetime.fromisoformat(e["started_at"])
                    for e in by_ch.get(ch["channel_id"], [])
                ],
                eq,
                at,
                plan_windows(NODE, ch["sensor_kind"]),
            ),
        }
        for ch, eq in zip(data["channels"], eqs)
    ]
    scores.sort(key=lambda s: (-s["score"], s["channel_id"]))
    return eqs, to_sql(eqs), scores


def selfcheck():
    data = json.loads((PROOF / "input.json").read_text())
    at = datetime.fromisoformat(DEFAULT_AT)
    eqs, sql, scores = run(data, at)
    assert (sql, scores) == run(data, at)[1:], "не детерминировано"
    assert sql != run(data, at, SEED + 1)[1], "seed ни на что не влияет"
    # утечки нет: без отказов паспорт тот же самый
    assert sql == run({**data, "episodes": []}, at)[1], "синтетика зависит от отказов"
    assert len(data["channels"]) == 188, len(data["channels"])
    assert sql.count("UPDATE smvu.channel SET equipment_id = (SELECT") == 188
    assert sql.count("INSERT INTO asset.equipment (") == 188
    assert len({e["equipment_no"] for e in eqs}) == 188
    assert all(0 <= s["score"] <= 1 and s["reasons"] for s in scores)
    assert any(s["score"] > 0.35 for s in scores), "реальные отказы не дошли до балла"
    gas = [e for e in eqs if e["kind"] == "DEGD"]
    assert gas and all(e["points"][0]["kind"] == "calib" for e in gas)
    # формулу балла проверяет python -m app.domain.sensor_risk; здесь — что сид
    # совпадает с файлом в поставке, иначе поправили генератор и забыли перегенерировать
    assert SEED_SQL.read_text() == sql, f"{SEED_SQL} отстал: перезапусти скрипт с input.json"
    assert sql.count("$synth$") == 2 and "BEGIN;" not in sql and "COMMIT;" not in sql
    print(
        f"selfcheck ok: 188 каналов, {sql.count('INTO asset.measurement (')} пачек измерений, "
        f"балл {scores[-1]['score']}..{scores[0]['score']}"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", nargs="?", help="JSON от --query")
    ap.add_argument("--at", default=DEFAULT_AT, help="момент расчёта балла")
    ap.add_argument("--out", type=Path, default=PROOF)
    ap.add_argument("--query", action="store_true", help="напечатать SELECT для стенда")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.query:
        return print(QUERY)
    if a.selfcheck:
        return selfcheck()
    if not a.input:
        sys.exit("нужен input.json (см. --query) или --selfcheck")
    _, sql, scores = run(
        json.loads(Path(a.input).read_text()), datetime.fromisoformat(a.at)
    )
    a.out.mkdir(parents=True, exist_ok=True)
    SEED_SQL.write_text(sql)
    (a.out / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=1) + "\n"
    )
    print(f"{SEED_SQL}, {a.out}/scores.json: {len(scores)} каналов")


if __name__ == "__main__":
    main()
