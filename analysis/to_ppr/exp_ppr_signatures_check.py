#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Независимая проверка замера «сигнатуры ППР» (exp_ppr_signatures.py / .csv /
_daily.csv). Проверяющий код — не копия исходного скрипта: агрегаты считаются
заново из выгрузок и из журнала, своими проходами.

Режимы:
  aggs    — независимый пересчёт агрегатов из exp_ppr_signatures_daily.csv и
            сверка с exp_ppr_signatures.csv (+ проверка метода: дубли, медиана,
            привязка объектов ППР, утечка будущего);
  case-a  — журнал: эталон 23.04.2026, 40 каналов «ГАЗ Д1…Д40» (914/915);
  case-b  — журнал: событие выгрузки с наибольшим числом каналов «Неисправен»;
  case-c  — журнал: событие выгрузки, помеченное как НЕ попавшее в окно ППР.

Журнальные режимы дополнительно пересчитывают дневные счётчики своего
коллектора за весь период и сверяют их с exp_ppr_signatures_daily.csv
(построчно) — это проверка самой дневной выгрузки, из которой считаются
агрегаты.

Запуск:
  .venv/bin/python analysis/to_ppr/exp_ppr_signatures_check.py aggs
  .venv/bin/python analysis/to_ppr/exp_ppr_signatures_check.py case-a
  .venv/bin/python analysis/to_ppr/exp_ppr_signatures_check.py case-b
  .venv/bin/python analysis/to_ppr/exp_ppr_signatures_check.py case-c
Ключи:  --dir <каталог с выгрузками> (по умолчанию analysis/to_ppr).

Всё пишет в stdout; отчёт-протокол — exp_ppr_signatures_check.md.
"""

import csv
import datetime
import os
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OBJ_REF = os.path.join(ROOT, "dataset", "справочник_объектов_диспетчер.csv")
CHAN_REF = os.path.join(ROOT, "dataset", "справочник_каналов_датчиков.csv")
JOURNAL = os.path.join(ROOT, "dataset", "ext-journal-2026.csv")
PPR_CSV = os.path.join(ROOT, "analysis", "to_ppr", "ppr_schedule.csv")
DEF_OUT = os.path.join(ROOT, "analysis", "to_ppr")

STATE_NEISPRAVEN = "Неисправен"
NEISP_MIN_CH = 5
NEISP_MIN_SHARE = 0.5
SILENCE_RATIO = 0.5
SILENCE_MIN_DAYS = 2
SILENCE_MIN_MEDIAN = 3
RETURN_RATIO = 0.9
MATCH_TOL = 2

STAGE_DEMONT = "начало_демонтажа_датчиков"
STAGE_VIVOZ = "вывоз_датчиков_из_ОМ"
STAGE_PRIEM = "сдача_работ_комиссии"

SIG_TYPES = ("всплеск Неиспр", "тишина")

# таблица mapping.md (коллектор -> (газоанализаторов, [кандидаты])) — как заявлено
MAPPING_MD = {
    "объект Эпсилон": (96, ["Объект 16"]),
    "Комплекс объект Дельта": (62, ["Объект 3"]),
    "объект Альфа": (55, ["Объект 1", "Объект 25", "Объект 26"]),
    "объект Кси": (55, ["Объект 1", "Объект 25", "Объект 26"]),
    "объект Зита": (48, ["Объект 4"]),
    "объект Омикрон": (41, ["Объект 9", "Объект 15"]),
    "объект Мю": (40, ["Объект 9", "Объект 15"]),
    "ПС объект Ро": (35, ["Объект 6", "Объект 23"]),
    "объект Каппа": (33, ["Объект 6", "Объект 14"]),
    "объект Гамма": (26, []),
    "объект Йота": (11, ["Объект 7"]),
    "объект Тау": (8, ["Объект 11"]),
    "ПС объект Омега": (8, ["Объект 11"]),
    "объект Вита": (6, ["Объект 10", "Объект 19"]),
    "объект Бета": (4, ["Объект 19", "Объект 24"]),
    "объект Сигма": (1, []),
}


def d(s):
    return datetime.date.fromisoformat(s).toordinal()


def iso(x):
    return datetime.date.fromordinal(x).isoformat()


# ---------------------------------------------------------------- справочники

def load_tree():
    tree = {}
    with open(OBJ_REF, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            tree[int(r["ид_объект"])] = (int(r["иерархия_уровень"]),
                                         int(r["родитель"]),
                                         r["диспетчерское_название_объекта"])
    return tree


def resolve_collector(oid, tree):
    for _ in range(10):
        node = tree.get(oid)
        if node is None:
            return None
        if node[0] == 2:
            return oid
        oid = node[1]
    return None


def load_channels(tree):
    """ид_канала -> (коллектор, листовой_объект, газ?, имя)."""
    chans = {}
    with open(CHAN_REF, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cid = int(r["ид_канала_данных"])
            oid = int(r["ид_объект"])
            node = tree.get(oid)
            leaf = oid if node is not None and node[0] == 3 else None
            coll = resolve_collector(oid, tree)
            is_gas = ("азо" in r["тип_датчика"])  # как в mapping.py
            chans[cid] = (coll, leaf, is_gas, r["название_датчика"],
                          r["тег_инженерной_системы"], r["тип_датчика"])
    return chans


def load_ppr():
    objs = {}
    with open(PPR_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            o = objs.setdefault(r["объект"], {"кол_датчиков": int(r["кол_датчиков_шт"]),
                                              "пакет": r["пакет_id"], "этапы": {}})
            if r["дата_этапа"]:
                o["этапы"][r["этап"]] = d(r["дата_этапа"])
    return objs


def ppr_windows(ppr):
    """(объект, пакет, начало-2, конец+2) по всем объектам графика."""
    out = []
    for o, v in ppr.items():
        s = v["этапы"].get(STAGE_DEMONT)
        e = v["этапы"].get(STAGE_PRIEM)
        if s and e:
            out.append((o, v["пакет"], s - MATCH_TOL, e + MATCH_TOL))
    return out


def in_union(day, windows):
    return any(ws <= day <= we for (o, pk, ws, we) in windows)


# --------------------------------------------------------------- дневные CSV

def read_daily(path):
    """[(дата_ord, уровень, имя, ид, строк, строк_газ, акт, акт_газ,
    стр_Неиспр, кан_Неиспр, стр_Неиспр_газ, кан_Неиспр_газ)]"""
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append((d(r["дата"]), r["уровень"], r["узел"], int(r["ид_узла"]),
                         int(r["строк"]), int(r["строк_газ"]),
                         int(r["активных_каналов"]), int(r["активных_газ"]),
                         int(r["строк_Неисправен"]), int(r["каналов_Неисправен"]),
                         int(r["строк_Неисправен_газ"]), int(r["каналов_Неисправен_газ"])))
    return rows


def read_events(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------- независимая пересборка S1/S2/S3

def month_medians(series, d0, d1):
    """Медиана активных каналов по каждому месяцу; дни без строк = 0."""
    med = {}
    m = datetime.date.fromordinal(d0).replace(day=1)
    while True:
        m0 = m.toordinal()
        nxt = (m.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        vals = [series.get(x, 0) for x in range(m0, nxt.toordinal()) if d0 <= x <= d1]
        if vals:
            med[m0] = statistics.median(vals)
        if nxt.toordinal() > d1:
            break
        m = nxt
    return med


def med_of(med, x):
    return med.get(datetime.date.fromordinal(x).replace(day=1).toordinal(), 0.0)


def recompute_events(daily_rows, gas_by_coll, rule):
    """rule='code'  — как реализовано в exp_ppr_signatures.py (гейт тишины:
    хотя бы один месяц с медианой >= 3, но порог считается по всем месяцам);
    rule='doc'   — как описано в докстринге (правило тишины только в месяцах
    с медианой >= 3). Возвращает список событий-словарей."""
    coll_days = defaultdict(lambda: {"act": {}, "act_g": {}, "nch": {}, "nch_g": {},
                                     "nrows": {}, "nrows_g": {}})
    names = {}
    for (day, lvl, name, nid, rows, rows_g, act, act_g, nrs, nch, nrs_g, nch_g) in daily_rows:
        if lvl != "коллектор":
            continue
        names[name] = nid
        dd = coll_days[name]
        dd["act"][day] = act
        dd["act_g"][day] = act_g
        dd["nch"][day] = nch
        dd["nch_g"][day] = nch_g
        dd["nrows"][day] = nrs
        dd["nrows_g"][day] = nrs_g

    events = []
    for coll, dd in coll_days.items():
        ngas = gas_by_coll.get(coll, 0)
        for scope in ("газ", "все"):
            act = dd["act_g"] if scope == "газ" else dd["act"]
            nch = dd["nch_g"] if scope == "газ" else dd["nch"]
            nrows = dd["nrows_g"] if scope == "газ" else dd["nrows"]
            days = sorted(act)
            if not days:
                continue
            d0, d1 = days[0], days[-1]
            med = month_medians(act, d0, d1)
            thr = min(NEISP_MIN_CH, NEISP_MIN_SHARE * ngas) if scope == "газ" else NEISP_MIN_CH

            for day in days:
                if nch.get(day, 0) >= thr:
                    events.append({"дата": day, "коллектор": coll, "область": scope,
                                   "тип": "всплеск Неиспр", "активных": act.get(day, 0),
                                   "медиана": med_of(med, day), "каналов": nch.get(day, 0),
                                   "строк": nrows.get(day, 0), "эпизод": 1})

            if rule == "code":
                if not any(v >= SILENCE_MIN_MEDIAN for v in med.values()):
                    continue
                month_ok = {m: True for m in med}
            else:  # 'doc'
                month_ok = {m: (v >= SILENCE_MIN_MEDIAN) for m, v in med.items()}
                if not any(month_ok.values()):
                    continue

            silent = [x for x in range(d0, d1 + 1)
                      if month_ok.get(datetime.date.fromordinal(x).replace(day=1).toordinal())
                      and act.get(x, 0) < SILENCE_RATIO * med_of(med, x)]
            runs, run = [], []
            for x in silent + [None]:
                if run and (x is None or x != run[-1] + 1):
                    runs.append(run)
                    run = []
                if x is not None:
                    run.append(x)
            for r0 in runs:
                if len(r0) < SILENCE_MIN_DAYS:
                    continue
                events.append({"дата": r0[0], "коллектор": coll, "область": scope,
                               "тип": "тишина", "активных": act.get(r0[0], 0),
                               "медиана": med_of(med, r0[0]),
                               "каналов": nch.get(r0[0], 0), "строк": nrows.get(r0[0], 0),
                               "эпизод": len(r0)})
                for x in range(r0[-1] + 1, d1 + 1):
                    if (med_of(med, x) >= SILENCE_MIN_MEDIAN
                            and act.get(x, 0) >= RETURN_RATIO * med_of(med, x)):
                        events.append({"дата": x, "коллектор": coll, "область": scope,
                                       "тип": "возврат", "активных": act.get(x, 0),
                                       "медиана": med_of(med, x),
                                       "каналов": nch.get(x, 0), "строк": nrows.get(x, 0),
                                       "эпизод": 1})
                        break
    return events


def ev_key(e):
    return (e["дата"], e["коллектор"], e["область"], e["тип"], e["эпизод"])


def ev_val(e):
    return (e["активных"], round(float(e["медиана"]), 1), e["каналов"], e["строк"])


# ------------------------------------------------------------------- режим aggs

def cmd_aggs(outdir):
    daily_path = os.path.join(outdir, "exp_ppr_signatures_daily.csv")
    events_path = os.path.join(outdir, "exp_ppr_signatures.csv")
    print(f"ИСТОЧНИКИ: {daily_path} / {events_path}")
    for p in (daily_path, events_path, os.path.join(ROOT, "analysis", "to_ppr",
                                                   "exp_ppr_signatures.py")):
        st = os.stat(p)
        import hashlib
        h = hashlib.md5(open(p, "rb").read()).hexdigest()
        print(f"  {os.path.basename(p)}  {st.st_size} байт  mtime="
              f"{datetime.datetime.fromtimestamp(st.st_mtime):%H:%M:%S}  md5={h}")

    tree = load_tree()
    chans = load_channels(tree)
    ppr = load_ppr()
    windows = ppr_windows(ppr)
    stages = sorted((sd, st, o, v["кол_датчиков"], v["пакет"])
                    for o, v in ppr.items() for st, sd in v["этапы"].items())

    collectors, gas_by_coll, chan_by_coll = {}, defaultdict(int), defaultdict(int)
    gas_by_name = defaultdict(int)
    coll_of_chan = defaultdict(set)
    for cid, (coll, leaf, is_gas, name, tag, ttype) in chans.items():
        if coll is None:
            continue
        collectors[coll] = tree[coll][2]
        chan_by_coll[coll] += 1
        coll_of_chan[coll].add(cid)
        if is_gas:
            gas_by_coll[coll] += 1
            gas_by_name[tree[coll][2]] += 1
    name_of = {v: k for k, v in collectors.items()}

    # ---- 0. привязка по числу датчиков против mapping.md ----
    print("\n=== 0. КАНДИДАТЫ ПРИВЯЗКИ: пересчёт против mapping.md ===")
    for coll, g in sorted(gas_by_coll.items(), key=lambda kv: -kv[1]):
        nm = collectors[coll]
        cand = sorted([o for o, v in ppr.items() if abs(v["кол_датчиков"] - g) <= 1],
                      key=lambda o: int(o.split()[1]))
        claimed = MAPPING_MD.get(nm)
        ok = claimed is not None and claimed[0] == g and claimed[1] == cand
        print(f"  {nm:<22} газ {g:>3} кандидаты: {', '.join(cand) or '—':<28}"
              f" mapping.md: {claimed}  {'OK' if ok else 'РАСХОЖДЕНИЕ'}")

    # ---- 1. события из двух выгрузок ----
    ev_rows = read_events(events_path)
    daily_rows = read_daily(daily_path)
    print(f"\n=== 1. ОБЪЁМ ВЫГРУЗОК ===")
    print(f"  exp_ppr_signatures.csv: {len(ev_rows)} строк-событий")
    print(f"  exp_ppr_signatures_daily.csv: {len(daily_rows)} строк "
          f"(коллекторы: {sum(1 for r in daily_rows if r[1] == 'коллектор')}, "
          f"листовые: {sum(1 for r in daily_rows if r[1] != 'коллектор')})")

    # ---- 2. независимая пересборка событий из daily ----
    print("\n=== 2. ПЕРЕСБОРКА СОБЫТИЙ ИЗ daily (правило 'code') ПРОТИВ CSV ===")
    mine = recompute_events(daily_rows, gas_by_name, "code")
    mine_d = {ev_key(e): e for e in mine}
    csv_d = {}
    for r in ev_rows:
        key = (d(r["дата"]), r["коллектор"], r["область"], r["тип_события"],
               int(r["эпизод_дней"]))
        csv_d[key] = (int(r["активных_каналов"]), round(float(r["медиана_месяца"]), 1),
                      int(r["каналов_Неисправен"]), int(r["строк_Неисправен"]))
    only_csv = [k for k in csv_d if k not in mine_d]
    only_mine = [k for k in mine_d if k not in csv_d]
    diff_val = [k for k in csv_d if k in mine_d and csv_d[k] != ev_val(mine_d[k])]
    print(f"  пересобрано событий: {len(mine)}, в CSV: {len(csv_d)}")
    print(f"  только в CSV: {len(only_csv)}   только у проверяющего: {len(only_mine)} "
          f"  поля не совпали: {len(diff_val)}")
    for k in only_csv[:15]:
        print(f"    ТОЛЬКО-CSV  {iso(k[0])} {k[1]} {k[2]} {k[3]} эп {k[4]} -> {csv_d[k]}")
    for k in only_mine[:15]:
        print(f"    ТОЛЬКО-ПРОВ  {iso(k[0])} {k[1]} {k[2]} {k[3]} эп {k[4]} -> {ev_val(mine_d[k])}")
    for k in diff_val[:15]:
        print(f"    ПОЛЯ  {iso(k[0])} {k[1]} {k[2]} {k[3]} эп {k[4]}: "
              f"CSV {csv_d[k]} vs проверка {ev_val(mine_d[k])}")

    mine_doc = recompute_events(daily_rows, gas_by_name, "doc")
    mine_doc_d = {ev_key(e): e for e in mine_doc}
    print(f"  вариант 'doc' (тишина только в месяцах с медианой >= 3): "
          f"{len(mine_doc)} событий, отличий от CSV: "
          f"{sum(1 for k in csv_d if k not in mine_doc_d)}")

    # сверка полей событий с daily (все строки CSV)
    daily_ix = {(r[0], r[2]): r for r in daily_rows if r[1] == "коллектор"}
    bad_fields = []
    zero_days = []
    for r in ev_rows:
        key = (d(r["дата"]), r["коллектор"])
        dr = daily_ix.get(key)
        if dr is None:
            # дня нет в daily = коллектор не записал ни строки; для событий-тишины
            # это норма (активность 0), для остальных — расхождение
            zero_days.append((r["дата"], r["коллектор"], r["область"], r["тип_события"],
                              int(r["активных_каналов"]), int(r["каналов_Неисправен"]),
                              int(r["строк_Неисправен"])))
            if (int(r["активных_каналов"]), int(r["каналов_Неисправен"]),
                    int(r["строк_Неисправен"])) != (0, 0, 0):
                bad_fields.append((r["дата"], r["коллектор"], r["область"],
                                   "нет строки дня, но значения не нулевые"))
            continue
        if r["область"] == "газ":
            exp = (dr[7], dr[11], dr[10])   # акт_газ, кан_Неиспр_газ, стр_Неиспр_газ
        else:
            exp = (dr[6], dr[9], dr[8])
        got = (int(r["активных_каналов"]), int(r["каналов_Неисправен"]),
               int(r["строк_Неисправен"]))
        if exp != got:
            bad_fields.append((r["дата"], r["коллектор"], r["область"],
                               f"CSV {got} vs daily {exp}"))
    print(f"  строк CSV, чьи поля не сходятся с daily: {len(bad_fields)}")
    for b in bad_fields[:15]:
        print(f"    ПОЛЯ-DAILY  {b}")
    print(f"  событий в дни, отсутствующие в daily (коллектор молчал): "
          f"{len(zero_days)}")
    for z in zero_days:
        print(f"    ПУСТОЙ-ДЕНЬ  {z}")

    # ---- 3. дубли и двойной счёт ----
    print("\n=== 3. ДВОЙНОЙ СЧЁТ В ВЫГРУЗКЕ ===")
    c_all = Counter((r["дата"], r["коллектор"], r["тип_события"]) for r in ev_rows)
    dup_scope = {k: v for k, v in c_all.items() if v > 1}
    print(f"  (дата, коллектор, тип) в двух областях (газ+все = 2 строки): "
          f"{len(dup_scope)} наборов, {sum(dup_scope.values())} строк")
    for k in sorted(dup_scope):
        print(f"    {k[0]} {k[1]} {k[2]}")
    c_pair = Counter((r["дата"], r["коллектор"]) for r in ev_rows
                     if r["тип_события"] in SIG_TYPES)
    multi = {k: v for k, v in c_pair.items() if v > 1}
    print(f"  (дата, коллектор) с числом событий S1+S2 > 1 (включая разные типы): "
          f"{len(multi)}")
    for k in sorted(multi):
        rows = [r for r in ev_rows if r["дата"] == k[0] and r["коллектор"] == k[1]
                and r["тип_события"] in SIG_TYPES]
        print(f"    {k[0]} {k[1]}: " + "; ".join(
            f"{r['область']}/{r['тип_события']} кан {r['каналов_Неисправен']}"
            for r in rows))
    c_full = Counter((r["дата"], r["коллектор"], r["область"], r["тип_события"])
                     for r in ev_rows)
    print(f"  полных дублей (дата, коллектор, область, тип): "
          f"{sum(1 for v in c_full.values() if v > 1)}")

    # ---- 4. четыре агрегата ----
    print("\n=== 4. ЧЕТЫРЕ АГРЕГАТА: заявлено выгрузкой vs пересчёт проверяющего ===")
    demounts = sorted((sd, o) for (sd, st, o, q, pk) in stages if st == STAGE_DEMONT)

    def sig_rows(rows, scope="газ"):
        return [r for r in rows if r["область"] == scope
                and r["тип_события"] in SIG_TYPES]

    # --- заявлено (из exp_ppr_signatures.csv)
    sig = sig_rows(ev_rows, "газ")
    claim_dem = [r for r in sig if r["совп_демонтаж_дней"] != ""]
    claim_dem_nearest = [r for r in sig if r["ближайший_этап"] == STAGE_DEMONT
                         and abs(int(r["сдвиг_дней"])) <= MATCH_TOL]
    claim_coll = sorted({r["коллектор"] for r in claim_dem})
    claim_fp = [r for r in sig if not r["окна_ППР"]]
    claim_fp_all = [r for r in ev_rows if r["тип_события"] in SIG_TYPES
                    and not r["окна_ППР"]]
    print(f"  [заявлено] событий с совп_демонтаж (газ S1+S2): {len(claim_dem)}"
          f"   (по ближайшему этапу=демонтаж: {len(claim_dem_nearest)})")
    for r in claim_dem:
        print(f"      {r['дата']} {r['коллектор']:<22} {r['тип_события']:<15} "
              f"сдвиг {r['совп_демонтаж_дней']:>2} кан {r['каналов_Неисправен']}")
    print(f"  [заявлено] коллекторов с такими событиями: {len(claim_coll)}"
          f" ({', '.join(claim_coll)})")
    uniq_coll_day = {(r["коллектор"], r["дата"]) for r in claim_dem}
    print(f"  [заявлено] уникальных (коллектор, день) среди них: {len(uniq_coll_day)}")
    print(f"  [заявлено] событий вне всех окон ППР: по газу {len(claim_fp)}, "
          f"по обеим областям {len(claim_fp_all)}")

    # --- пересчёт (мой, из daily + ppr_schedule)
    my_sig = [e for e in mine if e["область"] == "газ" and e["тип"] in SIG_TYPES]
    my_dem = [e for e in my_sig
              if any(abs(sd - e["дата"]) <= MATCH_TOL for sd, o in demounts)]
    my_coll = sorted({e["коллектор"] for e in my_dem})
    my_fp = [e for e in my_sig if not in_union(e["дата"], windows)]
    print(f"  [пересчёт] событий в ±2 от любого демонтажа (газ S1+S2): {len(my_dem)}")
    print(f"  [пересчёт] коллекторов: {len(my_coll)} ({', '.join(my_coll)})")
    print(f"  [пересчёт] событий вне всех окон (газ S1+S2): {len(my_fp)}")

    # строже: совпадение с демонтажом СВОЕГО кандидата
    cand = {nm: [o for o, v in ppr.items()
                 if abs(v["кол_датчиков"] - gas_by_name[nm]) <= 1]
            for nm in name_of}
    strict = []
    for e in my_sig:
        ds = [ppr[o]["этапы"].get(STAGE_DEMONT) for o in cand.get(e["коллектор"], [])]
        if any(sd is not None and abs(sd - e["дата"]) <= MATCH_TOL for sd in ds):
            strict.append(e)
    print(f"  [строго] событий в ±2 от демонтажа СВОЕГО кандидата: {len(strict)}"
          f" (коллекторы: {len({e['коллектор'] for e in strict})})")
    for e in my_sig:
        if e not in strict:
            near = [(abs(sd - e["дата"]), iso(sd), o) for sd, o in demounts
                    if abs(sd - e["дата"]) <= MATCH_TOL]
            print(f"      вне-своего: {iso(e['дата'])} {e['коллектор']} {e['тип']} "
                  f"демонтажи рядом: {near or 'нет'}")

    # --- канало-дни «Неисправен» в окнах
    tot = defaultdict(int)
    for (day, lvl, name, nid, rows, rows_g, act, act_g, nrs, nch, nrs_g, nch_g) in daily_rows:
        if lvl != "коллектор":
            continue
        tot["ch_all"] += nch
        tot["ch_gas"] += nch_g
        if in_union(day, windows):
            tot["ch_all_win"] += nch
            tot["ch_gas_win"] += nch_g
    lb_gas = sum(int(r["каналов_Неисправен"]) for r in sig if r["окна_ППР"])
    lb_all = sum(int(r["каналов_Неисправен"]) for r in ev_rows
                 if r["область"] == "все" and r["тип_события"] in SIG_TYPES
                 and r["окна_ППР"])
    print(f"  [пересчёт из daily] канало-дней «Неисправен» в окнах ППР ±2:")
    print(f"      газовые каналы:  {tot['ch_gas_win']}  (всего за период {tot['ch_gas']})")
    print(f"      все каналы:      {tot['ch_all_win']}  (всего {tot['ch_all']})")
    print(f"  [нижняя граница из событийной CSV] сумма каналов_Неисправен по строкам "
          f"S1+S2 в окнах: газ {lb_gas}, все {lb_all} (события ловят только дни "
          f"с >= порога каналов — полной суммы в событийной CSV нет)")
    per_coll = defaultdict(lambda: [0, 0, 0, 0])  # газ_вокнах, газ_всего, все_вокнах, все_всего
    for (day, lvl, name, nid, rows, rows_g, act, act_g, nrs, nch, nrs_g, nch_g) in daily_rows:
        if lvl != "коллектор":
            continue
        pc = per_coll[name]
        pc[1] += nch_g
        pc[3] += nch
        if in_union(day, windows):
            pc[0] += nch_g
            pc[2] += nch
    print("  канало-дни «Неисправен» по коллекторам (в окнах / всего, газ и все):")
    for nm in sorted(per_coll, key=lambda n: -per_coll[n][0]):
        pc = per_coll[nm]
        print(f"      {nm:<22} газ {pc[0]:>4}/{pc[1]:<4}  все {pc[2]:>5}/{pc[3]:<5}")

    # окна строго (без допуска) и по своему объекту — для полноты
    win_strict = [(o, pk, ws + MATCH_TOL, we - MATCH_TOL) for (o, pk, ws, we) in windows]
    own_w = {}
    for nm in name_of:
        for o in cand.get(nm, []):
            s = ppr[o]["этапы"].get(STAGE_DEMONT)
            e_ = ppr[o]["этапы"].get(STAGE_PRIEM)
            if s and e_ and o in [r["объект_ППР"] for r in sig if r["коллектор"] == nm]:
                own_w[nm] = (s - MATCH_TOL, e_ + MATCH_TOL)
    t_strict = t_own = t_own_g = 0
    for (day, lvl, name, nid, rows, rows_g, act, act_g, nrs, nch, nrs_g, nch_g) in daily_rows:
        if lvl != "коллектор":
            continue
        if in_union(day, win_strict):
            t_strict += nch
        w = own_w.get(name)
        if w and w[0] <= day <= w[1]:
            t_own += nch
            t_own_g += nch_g
    print(f"      для сравнения — все каналы в окнах строго (без ±2): {t_strict}")
    print(f"      ... в окне своего подтверждённого объекта (все/газ): {t_own}/{t_own_g}")

    # ---- 5. привязка события -> объект ППР: не тому ли объекту? ----
    print("\n=== 5. ПРИВЯЗКА К ОБЪЕКТУ ППР: НЕ ТОМУ ЛИ ОБЪЕКТУ? ===")
    stage_days = defaultdict(set)
    for (sd, st, o, q, pk) in stages:
        stage_days[sd].add(o)
    wrong = {"газ": 0, "все": 0}
    ambig = {"газ": 0, "все": 0}
    shown = {"газ": 0, "все": 0}
    for r in ev_rows:
        if r["тип_события"] not in SIG_TYPES or not r["объект_ППР"]:
            continue
        scope = r["область"]
        nm = r["коллектор"]
        cs = cand.get(nm, [])
        o = r["объект_ППР"]
        dd = d(r["дата_этапа"])
        a = len(stage_days.get(dd, ())) > 1
        if a:
            ambig[scope] += 1
        if o not in cs:
            wrong[scope] += 1
            if shown[scope] < 12:
                shown[scope] += 1
                print(f"    НЕ КАНДИДАТ [{scope}]: {r['дата']} {nm} "
                      f"({gas_by_name[nm]} газ) -> {o} ({ppr[o]['кол_датчиков']} датч.), "
                      f"этап {r['ближайший_этап']} {r['дата_этапа']}; кандидаты: "
                      f"{', '.join(cs) or '—'}"
                      f"{' [дата этапа общая для ' + ','.join(sorted(stage_days[dd])) + ']' if a else ''}")
    for scope in ("газ", "все"):
        print(f"  [{scope}] событий S1+S2, привязанных к НЕ кандидату по числу "
              f"датчиков: {wrong[scope]} из "
              f"{len([r for r in ev_rows if r['область'] == scope and r['тип_события'] in SIG_TYPES and r['объект_ППР']])}")
        print(f"  [{scope}] событий S1+S2 с неоднозначной датой этапа (несколько "
              f"объектов в один день): {ambig[scope]}")
    print(f"  (коллекторов без кандидата вовсе: "
          f"{[nm for nm in name_of if not cand[nm]]})")

    # ---- 6. метод: медиана, будущее, артефакты ----
    print("\n=== 6. МЕДИАНА МЕСЯЦА И УТЕЧКА БУДУЩЕГО ===")
    print("  медиана считается по КАЛЕНДАРНОМУ месяцу целиком (дни без строк = 0),")
    print("  т.е. событие в начале месяца использует данные всего месяца, включая")
    print("  ПОСЛЕДУЮЩИЕ дни. Проверка числом событий, чья медиана использует дни")
    print("  после даты события:")
    n_leak = 0
    for e in my_sig:
        m0 = datetime.date.fromordinal(e["дата"]).replace(day=1).toordinal()
        nxt = (datetime.date.fromordinal(m0).replace(day=28)
               + datetime.timedelta(days=4)).replace(day=1).toordinal()
        if nxt - 1 > e["дата"]:
            n_leak += 1
    print(f"    подписей газа S1+S2, где медиана месяца включает дни после события: "
          f"{n_leak} из {len(my_sig)}")
    changed = 0
    coll_days_x = defaultdict(dict)
    for (day, lvl, name, nid, rows, rows_g, act, act_g, nrs, nch, nrs_g, nch_g) in daily_rows:
        if lvl == "коллектор":
            coll_days_x[name][day] = (act, act_g)
    for nm, series in coll_days_x.items():
        for day, (act, act_g) in series.items():
            m0 = datetime.date.fromordinal(day).replace(day=1).toordinal()
            nxt = (datetime.date.fromordinal(m0).replace(day=28)
                   + datetime.timedelta(days=4)).replace(day=1).toordinal()
            fut = [v[0] for x, v in sorted(series.items()) if m0 <= x <= nxt - 1 and x > day]
            past = [series.get(x, (0, 0))[0] for x in range(m0, day)]
            if fut and past:
                med_past = statistics.median(past + [act])
                med_all = med_of(month_medians({x: v[0] for x, v in series.items()},
                                               min(series), max(series)), day)
                if (med_past < SILENCE_MIN_MEDIAN) != (med_all < SILENCE_MIN_MEDIAN):
                    changed += 1
    print(f"    (коллектор × день), где решение «медиана >= 3» различается между")
    print(f"    медианой по одним прошлым дням и медианой всего месяца: {changed}")

    art_days = sorted({day for (day, lvl, *_ ) in daily_rows if lvl == "коллектор"})
    art = [x for x in range(art_days[0], art_days[-1] + 1) if x not in set(art_days)]
    print(f"  дни без единой строки по парку (артефакты журнала): "
          f"{[iso(x) for x in art] or 'нет'}")
    art_rows = [r for r in ev_rows if r["артефакт_журнала"] == "да"]
    print(f"  строк CSV с пометкой артефакт_журнала=да: {len(art_rows)} "
          f"{[(r['дата'], r['коллектор'], r['тип_события']) for r in art_rows]}")

    # ---- 7. суммы daily против известных итогов ----
    print("\n=== 7. КОНТРОЛЬНЫЕ СУММЫ daily ===")
    print(f"  сумма «строк» по коллекторам: {sum(r[4] for r in daily_rows if r[1] == 'коллектор')}")
    print(f"  сумма «строк» по листовым:   {sum(r[4] for r in daily_rows if r[1] != 'коллектор')}")
    print(f"  сумма «строк_Неисправен» по коллекторам: "
          f"{sum(r[8] for r in daily_rows if r[1] == 'коллектор')}")
    # листовые не должны превышать коллектор своего дня
    leaf_over = 0
    coll_ix = defaultdict(dict)
    for r in daily_rows:
        if r[1] == "коллектор":
            coll_ix[r[2]][r[0]] = r
    tree2 = tree
    for r in daily_rows:
        if r[1] == "коллектор":
            continue
        coll = resolve_collector(r[3], tree2)
        cr = coll_ix.get(collectors.get(coll), {}).get(r[0])
        if cr and r[4] > cr[4]:
            leaf_over += 1
    print(f"  листовых строк, где строк больше, чем у коллектора того же дня: {leaf_over}")


# --------------------------------------------------------------- журнальные кейсы

def journal_pass(chan_ids, gas_ids=None):
    """Стриминг журнала; накапливает по дням для chan_ids.
    Возвращает (rows_by_day, neisp_by_day, neisp_rows_by_day, neisp_rows_gas_by_day,
    n_rows, n_unknown): rows_by_day[day] -> set(активных каналов),
    neisp_by_day[day] -> set(каналов с «Неисправен»),
    neisp_rows_by_day[day] -> строк «Неисправен» (все каналы группы),
    neisp_rows_gas_by_day[day] -> строк «Неисправен» по gas_ids."""
    gas_ids = gas_ids or set()
    rows_by_day = defaultdict(set)
    neisp_by_day = defaultdict(set)
    neisp_rows_by_day = defaultdict(int)
    neisp_rows_gas_by_day = defaultdict(int)
    rows_rows_by_day = defaultdict(int)
    rows_rows_gas_by_day = defaultdict(int)
    n_rows = n_unknown = 0
    with open(JOURNAL, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            n_rows += 1
            try:
                cid = int(row[1])
                ds = row[2]
            except (ValueError, IndexError):
                n_unknown += 1
                continue
            if cid not in chan_ids:
                continue
            day = d(ds)
            rows_by_day[day].add(cid)
            rows_rows_by_day[day] += 1
            if cid in gas_ids:
                rows_rows_gas_by_day[day] += 1
            if row[5] == STATE_NEISPRAVEN:
                neisp_by_day[day].add(cid)
                neisp_rows_by_day[day] += 1
                if cid in gas_ids:
                    neisp_rows_gas_by_day[day] += 1
    return (rows_by_day, neisp_by_day, neisp_rows_by_day, neisp_rows_gas_by_day,
            rows_rows_by_day, rows_rows_gas_by_day, n_rows, n_unknown)


def daily_index(outdir, node_name):
    ix = {}
    for r in read_daily(os.path.join(outdir, "exp_ppr_signatures_daily.csv")):
        if r[1] == "коллектор" and r[2] == node_name:
            ix[r[0]] = r
    return ix


def cmd_case(outdir, which):
    tree = load_tree()
    chans = load_channels(tree)
    ppr = load_ppr()
    windows = ppr_windows(ppr)
    name_of = {}
    gas_by_coll = defaultdict(int)
    coll_chans = defaultdict(set)
    for cid, (coll, leaf, is_gas, name, tag, ttype) in chans.items():
        if coll is None:
            continue
        name_of[tree[coll][2]] = coll
        coll_chans[tree[coll][2]].add(cid)
        if is_gas:
            gas_by_coll[tree[coll][2]] += 1

    ev_rows = read_events(os.path.join(outdir, "exp_ppr_signatures.csv"))

    if which == "case-a":
        # эталон: 40 каналов ГАЗ Д1…Д40 префиксов 914/915
        grp = {cid for cid, (coll, leaf, is_gas, name, tag, ttype) in chans.items()
               if tag.startswith(("914", "915")) and name.startswith("ГАЗ Д")}
        node = "объект Мю"
        print(f"=== CASE-A: эталон 23.04.2026, {node}, 40 каналов ГАЗ Д* (914/915) ===")
        print(f"  каналов в группе по справочнику: {len(grp)}")
        names = {cid: chans[cid][3] for cid in grp}
        rbd, nbd, nrd, nrgd, rrd, rrgd, n_rows, n_unknown = journal_pass(grp, grp)
        print(f"  строк журнала всего: {n_rows}, распознать не удалось: {n_unknown}")
        day = d("2026-04-23")
        ch_n = nbd.get(day, set())
        print(f"  23.04.2026: каналов с «Неисправен»: {len(ch_n)}, "
              f"строк «Неисправен»: {nrd.get(day, 0)}, активных каналов в группе: "
              f"{len(rbd.get(day, ()))}, строк всего: {rrgd.get(day, 0)}")
        print(f"  НЕ записали «Неисправен»: "
              f"{sorted(names[c] for c in grp - ch_n)}")
        print(f"  строк «Неисправен» != числу каналов: "
              f"{nrd.get(day, 0) != len(ch_n)}")
        print("  профиль группы 18–30.04 (строк всего / активных каналов / "
              "строк «Неиспр» / каналов «Неиспр»):")
        for x in range(d("2026-04-18"), d("2026-04-30") + 1):
            print(f"    {iso(x)}: {rrgd.get(x, 0)} / {len(rbd.get(x, ()))} / "
                  f"{nrd.get(x, 0)} / {len(nbd.get(x, ()))}")
        apr = [x for x in rbd if datetime.date.fromordinal(x).month == 4]
        print(f"  апрель целиком: строк всего {sum(rrgd.get(x, 0) for x in apr)}, "
              f"строк «Неисправен» {sum(nrd.get(x, 0) for x in apr)}, "
              f"канало-дней «Неисправен» {sum(len(nbd.get(x, ())) for x in apr)}")
        # сверка с daily по газовым колонкам «объект Мю» за весь период
        ix = daily_index(outdir, node)
        bad = []
        for x in sorted(set(ix) | set(rbd)):
            dr = ix.get(x)
            exp_a = (rrgd.get(x, 0), len(rbd.get(x, ())), len(nbd.get(x, ())),
                     nrd.get(x, 0))
            got_a = (dr[5], dr[7], dr[11], dr[10]) if dr else None
            if got_a != exp_a:
                bad.append((x, exp_a, got_a))
        print(f"  сверка с daily (строк_газ, активных_газ, каналов_Неиспр_газ, "
              f"строк_Неиспр_газ) по всем дням: расхождений {len(bad)}")
        for x, e_, g_ in bad[:10]:
            print(f"    {iso(x)}: журнал {e_} vs daily {g_}")
        # сверка со строками события 23.04
        for r in ev_rows:
            if r["дата"] == "2026-04-23" and r["коллектор"] == node:
                print(f"  строка события CSV: {r['область']} {r['тип_события']} "
                      f"акт {r['активных_каналов']} кан {r['каналов_Неисправен']} "
                      f"стр {r['строк_Неисправен']} (газ: журнал дал "
                      f"{len(ch_n)}/{nrd.get(day, 0)})")

    elif which == "case-b":
        # событие с наибольшим числом каналов «Неисправен» (кроме эталона 23.04 Мю)
        cand_rows = [r for r in ev_rows if r["тип_события"] in SIG_TYPES
                     and not (r["дата"] == "2026-04-23" and r["коллектор"] == "объект Мю")]
        cand_rows.sort(key=lambda r: (-int(r["каналов_Неисправен"]),
                                      -int(r["строк_Неисправен"]), r["дата"]))
        r0 = cand_rows[0]
        node = r0["коллектор"]
        day = d(r0["дата"])
        grp = coll_chans[node]
        print(f"=== CASE-B: максимум «Неисправен»-каналов вне эталона ===")
        print(f"  выбрано из CSV: {r0['дата']} {node} ({r0['область']}) "
              f"{r0['тип_события']} кан {r0['каналов_Неисправен']} "
              f"стр {r0['строк_Неисправен']}; каналов коллектора: {len(grp)} "
              f"(газовых {gas_by_coll[node]})")
        gas_ids = {c for c in grp if chans[c][2]}
        rbd, nbd, nrd, nrgd, rrd, rrgd, n_rows, n_unknown = journal_pass(grp, gas_ids)
        print(f"  строк журнала всего: {n_rows}, распознать не удалось: {n_unknown}")
        ch_n = nbd.get(day, set())
        print(f"  {r0['дата']} по журналу: каналов с «Неисправен»: {len(ch_n)}, "
              f"строк «Неисправен»: {nrd.get(day, 0)}, активных каналов: "
              f"{len(rbd.get(day, ()))}, строк всего: {rrd.get(day, 0)}")
        print(f"  из них газовых каналов «Неисправен»: {len(ch_n & gas_ids)}, "
              f"газовых строк «Неисправен»: {nrgd.get(day, 0)}")
        print(f"  сверка с CSV: кан {r0['каналов_Неисправен']} vs {len(ch_n)} -> "
              f"{'OK' if int(r0['каналов_Неисправен']) == len(ch_n) else 'РАСХОЖДЕНИЕ'}; "
              f"стр {r0['строк_Неисправен']} vs {nrd.get(day, 0)} -> "
              f"{'OK' if int(r0['строк_Неисправен']) == nrd.get(day, 0) else 'РАСХОЖДЕНИЕ'}")
        print("  контекст ±3 дня (строк всего / активных каналов / строк «Неиспр» / "
              "каналов «Неиспр»):")
        for x in range(day - 3, day + 4):
            print(f"    {iso(x)}: {rrd.get(x, 0)} / {len(rbd.get(x, ()))} / "
                  f"{nrd.get(x, 0)} / {len(nbd.get(x, ()))}")
        # сверка с daily за весь период (все колонки)
        ix = daily_index(outdir, node)
        bad = []
        for x in sorted(set(ix) | set(rbd)):
            dr = ix.get(x)
            exp_a = (rrd.get(x, 0), len(rbd.get(x, ())), len(nbd.get(x, ())),
                     nrd.get(x, 0))
            got_a = (dr[4], dr[6], dr[9], dr[8]) if dr else None
            if got_a != exp_a:
                bad.append((x, exp_a, got_a))
        print(f"  сверка с daily (строк, активных, каналов_Неиспр, строк_Неиспр) "
              f"по всем дням: расхождений {len(bad)}")
        for x, e_, g_ in bad[:10]:
            print(f"    {iso(x)}: журнал {e_} vs daily {g_}")

    else:  # case-c
        fp = [r for r in ev_rows if r["тип_события"] in SIG_TYPES and not r["окна_ППР"]]
        fp.sort(key=lambda r: (-int(r["каналов_Неисправен"]),
                               -int(r["строк_Неисправен"]), r["дата"]))
        r0 = fp[0]
        node = r0["коллектор"]
        day = d(r0["дата"])
        ep = int(r0["эпизод_дней"])
        grp = coll_chans[node]
        print(f"=== CASE-C: событие вне всех окон ППР ===")
        print(f"  выбрано из CSV: {r0['дата']} {node} ({r0['область']}) "
              f"{r0['тип_события']} кан {r0['каналов_Неисправен']} "
              f"стр {r0['строк_Неисправен']} эпизод {ep} дн")
        gas_ids = {c for c in grp if chans[c][2]}
        rbd, nbd, nrd, nrgd, rrd, rrgd, n_rows, n_unknown = journal_pass(grp, gas_ids)
        print(f"  строк журнала всего: {n_rows}, распознать не удалось: {n_unknown}")
        if r0["тип_события"] == "всплеск Неиспр":
            ch_n = nbd.get(day, set())
            print(f"  {r0['дата']} по журналу: каналов «Неисправен» {len(ch_n)}, "
                  f"строк «Неисправен» {nrd.get(day, 0)} -> сверка кан "
                  f"{'OK' if len(ch_n) == int(r0['каналов_Неисправен']) else 'РАСХОЖДЕНИЕ'}, "
                  f"стр "
                  f"{'OK' if nrd.get(day, 0) == int(r0['строк_Неисправен']) else 'РАСХОЖДЕНИЕ'}")
        else:
            print(f"  тишина: строк по дням эпизода и рядом:")
        lo, hi = day - 5, day + ep + 4
        for x in range(lo, hi + 1):
            mark = " <-- эпизод" if day <= x < day + ep else ""
            print(f"    {iso(x)}: строк {rrd.get(x, 0)}, активных каналов "
                  f"{len(rbd.get(x, ()))}, «Неиспр» {nrd.get(x, 0)} на "
                  f"{len(nbd.get(x, ()))} кан.{mark}")
        # окна: расстояние до ближайшего окна ППР, пересчёт из ppr_schedule
        near = []
        for o, v in ppr.items():
            s = v["этапы"].get(STAGE_DEMONT)
            e_ = v["этапы"].get(STAGE_PRIEM)
            if s and e_:
                ws, we = s - MATCH_TOL, e_ + MATCH_TOL
                dist = 0 if ws <= day <= we else (ws - day if day < ws else day - we)
                near.append((abs(dist), dist, o, v["пакет"], iso(ws), iso(we)))
        near.sort()
        print("  ближайшие окна ППР (пересчёт из ppr_schedule.csv):")
        for a_, dist, o, pk, ws, we in near[:5]:
            print(f"    {o}/{pk}: окно {ws}..{we}, дистанция до события {dist} дн")
        inw = [t for t in near if t[1] == 0]
        print(f"  окон, покрывающих дату события: {len(inw)} -> "
              f"{'OK: событие действительно вне окон' if not inw else 'РАСХОЖДЕНИЕ'}")
        # сверка с daily за весь период
        ix = daily_index(outdir, node)
        bad = []
        for x in sorted(set(ix) | set(rbd)):
            dr = ix.get(x)
            exp_a = (rrd.get(x, 0), len(rbd.get(x, ())), len(nbd.get(x, ())),
                     nrd.get(x, 0))
            got_a = (dr[4], dr[6], dr[9], dr[8]) if dr else None
            if got_a != exp_a:
                bad.append((x, exp_a, got_a))
        print(f"  сверка с daily (строк, активных, каналов_Неиспр, строк_Неиспр) "
              f"по всем дням: расхождений {len(bad)}")
        for x, e_, g_ in bad[:10]:
            print(f"    {iso(x)}: журнал {e_} vs daily {g_}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outdir = DEF_OUT
    if "--dir" in sys.argv:
        outdir = sys.argv[sys.argv.index("--dir") + 1]
    mode = args[0] if args else "aggs"
    if mode == "aggs":
        cmd_aggs(outdir)
    elif mode in ("case-a", "case-b", "case-c"):
        cmd_case(outdir, mode)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
