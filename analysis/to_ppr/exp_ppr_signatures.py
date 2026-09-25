#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эксперимент: «подписи» плановых работ ППР в журнале СМВУ 2026 по всем 16 нашим
коллекторам — и подтверждение привязки «наш коллектор ↔ объект графика ППР»
поведением каналов, а не только числом датчиков.

Метод (образец — разбор коллектора «объект Мю» / «к-р Ясенево», exp_yasenevo.py):
план ППР «Объект 9» имеет «начало демонтажа датчиков» 23.04.2026, и в журнале
23.04.2026 ровно 39 из 40 его газовых каналов пишут «Неисправен», затем каналы
молчат 6 суток. Такая «подпись» (всплеск «Неисправен» + тишина) должна быть
видна и у остальных коллекторов в даты их этапов ППР.

Один проход по журналу 2026 (30.7 млн строк, stdlib csv, без pandas) даёт по
каждому дню для каждого нашего коллектора (16 узлов уровня 2; каналы собраны по
дереву: листовой объект уровня 3 -> родитель-коллектор; листовые объекты
считаются отдельно) счётчики: строк всего, строк «Неисправен», каналов,
записавших «Неисправен» за день, активных каналов за день — в двух областях:
«газ» (только газовые каналы — то, что снимают по ППР) и «все» (контроль).

ПОДПИСИ-СОБЫТИЯ (область «газ»; контроль — те же правила в области «все»):
  S1 «всплеск Неисправен» — >= 5 газ-каналов записали «Неисправен» за день
      ИЛИ таких каналов >= 50% газового парка коллектора (малые объекты);
  S2 «тишина» — эпизод >= 2 подряд дней, где активных газ-каналов < 0.5 от
      медианы активных за месяц («демонтаж -> тишина»). Правило применимо,
      если медиана месяца >= 3 (иначе газовый поток коллектора разрежен и
      подпись не определена — такие коллекторы помечаются отдельно);
  S3 «возврат» — первый день после эпизода S2, где активных газ-каналов
      >= 0.9 медианы месяца (сверяется со стадией «вывоз из ОМ» / «приёмка»).
Прочие наблюдения (аномалии разреженного потока) — в отчёте, не как события.

Сопоставление: этапы графика ППР (analysis/to_ppr/ppr_schedule.csv), допуск
±2 дня от даты события. Привязка «коллектор ↔ объект ППР» сверяется с
кандидатами по числу датчиков (mapping.md, допуск ±1) и считается
подтверждённой поведением, если S1/S2 совпали с демонтажом кандидата.

Цена для обучающей метки: «дней-событий» и «канало-дней Неисправен»,
упавших в окна ППР янв–июн 2026 (по всем окнам и по окну своего объекта).
Контроль ложных срабатываний: события вне всех окон ППР.

Запуск:  .venv/bin/python analysis/to_ppr/exp_ppr_signatures.py
         .venv/bin/python analysis/to_ppr/exp_ppr_signatures.py --reuse-daily
         (второй вариант берёт дневные счётчики из уже готового
          exp_ppr_signatures_daily.csv и не перечитывает журнал).

Выгрузки: exp_ppr_signatures.csv (события), exp_ppr_signatures_daily.csv
(дневные счётчики по коллекторам и листовым объектам).
"""

import csv
import datetime
import os
import statistics
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OBJ_REF = os.path.join(ROOT, "dataset", "справочник_объектов_диспетчер.csv")
CHAN_REF = os.path.join(ROOT, "dataset", "справочник_каналов_датчиков.csv")
JOURNAL = os.path.join(ROOT, "dataset", "ext-journal-2026.csv")
PPR_CSV = os.path.join(ROOT, "analysis", "to_ppr", "ppr_schedule.csv")
OUT = os.path.join(ROOT, "analysis", "to_ppr")
DAILY_CSV = os.path.join(OUT, "exp_ppr_signatures_daily.csv")
EVENTS_CSV = os.path.join(OUT, "exp_ppr_signatures.csv")

STATE_NEISPRAVEN = "Неисправен"
NEISP_MIN_CH = 5          # S1: >= 5 каналов «Неисправен» за день ...
NEISP_MIN_SHARE = 0.5     # ... либо >= 50% газовых каналов коллектора
SILENCE_RATIO = 0.5       # S2: активных каналов < 0.5 медианы месяца
SILENCE_MIN_DAYS = 2      # S2: эпизод минимум из 2 дней
SILENCE_MIN_MEDIAN = 3    # S2: применимо при медиане месяца >= 3
RETURN_RATIO = 0.9        # S3: возврат при активности >= 0.9 медианы месяца
MATCH_TOL = 2             # допуск сопоставления с этапом ППР, дней
JOURNAL_END = "2026-06-30"

STAGE_DEMONT = "начало_демонтажа_датчиков"
STAGE_VIVOZ = "вывоз_датчиков_из_ОМ"
STAGE_PRIEM = "сдача_работ_комиссии"

I_ROWS, I_ROWS_G, I_NEISP, I_NEISP_G, I_ACT, I_ACT_G, I_NCH, I_NCH_G = range(8)


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
    chans = {}
    with open(CHAN_REF, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cid = int(r["ид_канала_данных"])
            oid = int(r["ид_объект"])
            node = tree.get(oid)
            leaf = oid if node is not None and node[0] == 3 else None
            coll = resolve_collector(oid, tree)
            is_gas = (r["тип_датчика"] == "Газовый датчик"
                      or r["тип_инж_системы"] == "Газовая охрана")
            chans[cid] = (coll, leaf, is_gas)
    return chans


def load_ppr():
    objs = {}
    with open(PPR_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            o = objs.setdefault(r["объект"], {"кол_датчиков": int(r["кол_датчиков_шт"]),
                                              "пакет": r["пакет_id"], "этапы": {}})
            if r["дата_этапа"]:
                o["этапы"][r["этап"]] = datetime.date.fromisoformat(r["дата_этапа"]).toordinal()
    return objs


# ------------------------------------------------------- проход по журналу

def journal_pass(chans):
    """Один проход: (день, узел) -> [строк, строк_газ, строк_Неиспр,
    строк_Неиспр_газ, {активные}, {активные_газ}, {Неиспр}, {Неиспр_газ}]."""
    nodes_of = {}
    for cid, (coll, leaf, is_gas) in chans.items():
        ns = []
        if coll is not None:
            ns.append(("C", coll))
        if leaf is not None:
            ns.append(("L", leaf))
        nodes_of[cid] = ns

    rec = {}
    date_cache = {}
    n_rows = 0
    unknown_rows = 0
    with open(JOURNAL, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            n_rows += 1
            try:
                cid = int(row[1])
                ds = row[2]
            except (ValueError, IndexError):
                unknown_rows += 1
                continue
            day = date_cache.get(ds)
            if day is None:
                day = datetime.date(int(ds[0:4]), int(ds[5:7]), int(ds[8:10])).toordinal()
                date_cache[ds] = day
            neisp = (row[5] == STATE_NEISPRAVEN)
            ns = nodes_of.get(cid)
            if not ns:
                unknown_rows += 1
                continue
            gas = chans[cid][2]
            for node in ns:
                key = (day, node)
                r = rec.get(key)
                if r is None:
                    r = [0, 0, 0, 0, set(), set(), set(), set()]
                    rec[key] = r
                r[I_ROWS] += 1
                if gas:
                    r[I_ROWS_G] += 1
                r[I_ACT].add(cid)
                if gas:
                    r[I_ACT_G].add(cid)
                if neisp:
                    r[I_NEISP] += 1
                    r[I_NCH].add(cid)
                    if gas:
                        r[I_NEISP_G] += 1
                        r[I_NCH_G].add(cid)
            if n_rows % 5_000_000 == 0:
                print(f"... {n_rows/1e6:.0f} млн строк", flush=True)
    print(f"строк журнала: {n_rows:,} (вне справочника: {unknown_rows})".replace(",", " "))
    return rec


def write_daily(rec, collectors, leaves):
    with open(DAILY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["дата", "уровень", "узел", "ид_узла", "строк", "строк_газ",
                    "активных_каналов", "активных_газ", "строк_Неисправен",
                    "каналов_Неисправен", "строк_Неисправен_газ", "каналов_Неисправен_газ"])
        for (day, node) in sorted(rec):
            r = rec[(day, node)]
            lvl, nid = node
            name = collectors[nid] if lvl == "C" else leaves.get(nid, str(nid))
            w.writerow([datetime.date.fromordinal(day).isoformat(),
                        "коллектор" if lvl == "C" else "листовой объект",
                        name, nid, r[I_ROWS], r[I_ROWS_G], len(r[I_ACT]), len(r[I_ACT_G]),
                        r[I_NEISP], len(r[I_NCH]), r[I_NEISP_G], len(r[I_NCH_G])])


def read_daily():
    """(день, узел) -> счётчики — обратная сторона write_daily."""
    rec = {}
    with open(DAILY_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["уровень"] != "коллектор":
                continue
            name = r["узел"]
            day = datetime.date.fromisoformat(r["дата"]).toordinal()
            rec[(day, ("C", name))] = [
                int(r["строк"]), int(r["строк_газ"]), int(r["строк_Неисправен"]),
                int(r["строк_Неисправен_газ"]),
                int(r["активных_каналов"]), int(r["активных_газ"]),
                int(r["каналов_Неисправен"]), int(r["каналов_Неисправен_газ"])]
    return rec


# ------------------------------------------------------------- анализ

def month_medians(series, d0, d1):
    med = {}
    m = datetime.date.fromordinal(d0).replace(day=1)
    while True:
        m0 = m.toordinal()
        nxt = (m.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        vals = [series.get(d, 0) for d in range(m0, nxt.toordinal()) if d0 <= d <= d1]
        if vals:
            med[m0] = statistics.median(vals)
        if nxt.toordinal() > d1:
            break
        m = nxt
    return med


def med_of(med, d):
    return med.get(datetime.date.fromordinal(d).replace(day=1).toordinal(), 0.0)


def main():
    reuse = "--reuse-daily" in sys.argv
    tree = load_tree()
    chans = load_channels(tree)
    ppr = load_ppr()
    end_ord = datetime.date.fromisoformat(JOURNAL_END).toordinal()

    collectors, leaves = {}, {}
    gas_by_coll, chan_by_coll = defaultdict(int), defaultdict(int)
    for cid, (coll, leaf, is_gas) in chans.items():
        if coll is None:
            continue
        collectors[coll] = tree[coll][2]
        chan_by_coll[coll] += 1
        if is_gas:
            gas_by_coll[coll] += 1
        if leaf is not None:
            leaves[leaf] = tree[leaf][2]

    print("=== НАШИ КОЛЛЕКТОРЫ ===")
    for coll in sorted(collectors, key=lambda c: -gas_by_coll[c]):
        print(f"  {collectors[coll]:<22} каналов {chan_by_coll[coll]:>5}, газовых {gas_by_coll[coll]:>3}")

    cand = {}
    for coll in collectors:
        g = gas_by_coll[coll]
        cand[coll] = sorted([o for o, v in ppr.items() if abs(v["кол_датчиков"] - g) <= 1],
                            key=lambda o: int(o.split()[1]))

    if reuse and os.path.exists(DAILY_CSV):
        print("\n(дневные счётчики взяты из exp_ppr_signatures_daily.csv)")
        rec = read_daily()
        # в режиме reuse ключи узлов — имена, приводим к ид через словарь имён
        name2id = {v: k for k, v in collectors.items()}
        rec = {(d, ("C", name2id[n[1]])): v for (d, n), v in rec.items()}
    else:
        rec = journal_pass(chans)
        write_daily(rec, collectors, leaves)

    # дневные ряды коллекторов (газ + все); в режиме reuse значения — числа,
    # после прохода по журналу — множества каналов
    def cnt(x):
        return len(x) if isinstance(x, set) else x

    coll_days = defaultdict(lambda: {"act": {}, "act_g": {}, "nch": {}, "nch_g": {},
                                     "nrows": {}, "nrows_g": {}})
    for (day, node), r in rec.items():
        if node[0] != "C":
            continue
        d = coll_days[node[1]]
        d["act"][day] = cnt(r[I_ACT])
        d["act_g"][day] = cnt(r[I_ACT_G])
        d["nch"][day] = cnt(r[I_NCH])
        d["nch_g"][day] = cnt(r[I_NCH_G])
        d["nrows"][day] = r[I_NEISP]
        d["nrows_g"][day] = r[I_NEISP_G]

    # дни без единой строки по парку — артефакты журнала
    days_seen = defaultdict(int)
    for (day, node), r in rec.items():
        if node[0] == "C":
            days_seen[day] += 1
    all_days = [d for d in range(min(days_seen), max(days_seen) + 1)]
    artifact_days = [d for d in all_days if days_seen.get(d, 0) == 0]
    print("\n=== АРТЕФАКТЫ ЖУРНАЛА (день без строк по всему парку) ===")
    print("  " + (", ".join(datetime.date.fromordinal(d).isoformat() for d in artifact_days) or "нет"))

    # ---------------- события-подписи ----------------
    events = []
    sparse = []
    for coll in sorted(coll_days):
        d = coll_days[coll]
        ngas = gas_by_coll[coll]
        for scope in ("газ", "все"):
            act = d["act_g"] if scope == "газ" else d["act"]
            nch = d["nch_g"] if scope == "газ" else d["nch"]
            nrows = d["nrows_g"] if scope == "газ" else d["nrows"]
            days = sorted(act)
            if not days:
                continue
            d0, d1 = days[0], days[-1]
            med = month_medians(act, d0, d1)

            # S1 — всплеск «Неисправен»: >= 5 каналов ИЛИ >= 50% парка
            thr = min(NEISP_MIN_CH, NEISP_MIN_SHARE * ngas) if scope == "газ" else NEISP_MIN_CH
            for day in days:
                if nch.get(day, 0) >= thr:
                    events.append({"дата": day, "коллектор": coll, "область": scope,
                                   "тип": "всплеск Неиспр", "активных": act.get(day, 0),
                                   "медиана": med_of(med, day),
                                   "каналов_Неиспр": nch.get(day, 0),
                                   "строк_Неиспр": nrows.get(day, 0), "эпизод_дней": 1})

            # S2 — тишина (эпизоды); применимо при медиане месяца >= SILENCE_MIN_MEDIAN
            med_ok = {m: v for m, v in med.items() if v >= SILENCE_MIN_MEDIAN}
            if not med_ok:
                if scope == "газ":
                    sparse.append(coll)
                continue
            silent = [day for day in range(d0, d1 + 1)
                      if act.get(day, 0) < SILENCE_RATIO * med_of(med, day)]
            run = []
            for day in silent + [None]:
                if run and (day is None or day != run[-1] + 1):
                    if len(run) >= SILENCE_MIN_DAYS:
                        events.append({"дата": run[0], "коллектор": coll, "область": scope,
                                       "тип": "тишина", "активных": act.get(run[0], 0),
                                       "медиана": med_of(med, run[0]),
                                       "каналов_Неиспр": nch.get(run[0], 0),
                                       "строк_Неиспр": nrows.get(run[0], 0),
                                       "эпизод_дней": len(run),
                                       "тишина_с": run[0], "тишина_по": run[-1]})
                    run = []
                if day is not None:
                    run.append(day)

            # S3 — возврат после тишины (не на пустых/артефактных днях)
            for ev in [e for e in events
                       if e["коллектор"] == coll and e["область"] == scope
                       and e["тип"] == "тишина"]:
                for day in range(ev["тишина_по"] + 1, d1 + 1):
                    if (med_of(med, day) >= SILENCE_MIN_MEDIAN
                            and act.get(day, 0) >= RETURN_RATIO * med_of(med, day)):
                        events.append({"дата": day, "коллектор": coll, "область": scope,
                                       "тип": "возврат", "активных": act.get(day, 0),
                                       "медиана": med_of(med, day),
                                       "каналов_Неиспр": nch.get(day, 0),
                                       "строк_Неиспр": nrows.get(day, 0),
                                       "эпизод_дней": 1, "после_тишины_с": ev["тишина_с"]})
                        break

    print("\n=== КОЛЛЕКТОРЫ С РАЗРЕЖЕННЫМ ГАЗОВЫМ ПОТОКОМ (правило тишины неприменимо) ===")
    print("  " + (", ".join(collectors[c] for c in sorted(set(sparse))) or "нет"))

    # ---------------- сопоставление с этапами ППР ----------------
    stages = []
    for o, v in ppr.items():
        for st, day in v["этапы"].items():
            stages.append((day, st, o, v["кол_датчиков"], v["пакет"]))
    stages.sort()
    windows = []
    for o, v in ppr.items():
        s = v["этапы"].get(STAGE_DEMONT)
        e = v["этапы"].get(STAGE_PRIEM)
        if s and e:
            windows.append((o, v["пакет"], s - MATCH_TOL, e + MATCH_TOL, s, e))

    for ev in events:
        day = ev["дата"]
        near = sorted((abs(sd - day), sd, st, o, q, pk)
                      for (sd, st, o, q, pk) in stages if abs(sd - day) <= MATCH_TOL)
        if near:
            _, sd, st, o, q, pk = near[0]
            ev.update({"этап": st, "дата_этапа": sd, "объект_ППР": o,
                       "датчиков_в_объекте": q, "пакет": pk, "сдвиг_дней": sd - day})
        else:
            ev.update({"этап": "", "дата_этапа": 0, "объект_ППР": "",
                       "датчиков_в_объекте": "", "пакет": "", "сдвиг_дней": ""})
        dm = [sd - day for (sd, st, o, q, pk) in stages
              if st == STAGE_DEMONT and abs(sd - day) <= MATCH_TOL]
        vz = [sd - day for (sd, st, o, q, pk) in stages
              if st == STAGE_VIVOZ and abs(sd - day) <= MATCH_TOL]
        ev["совп_демонтаж"] = min(dm, key=abs) if dm else ""
        ev["совп_вывоз"] = min(vz, key=abs) if vz else ""
        ev["окна_ППР"] = ";".join(sorted({f"{o}/{pk}" for (o, pk, ws, we, s, e) in windows
                                          if ws <= day <= we}))
        ev["артефакт_журнала"] = "да" if day in artifact_days else ""

    # ---------------- сводка привязки ----------------
    sig_days = defaultdict(set)   # коллектор -> дни подписей S1/S2 (газ)
    for ev in events:
        if ev["область"] == "газ" and ev["тип"] in ("всплеск Неиспр", "тишина"):
            sig_days[ev["коллектор"]].add(ev["дата"])

    print("\n=== СВОДКА ПРИВЯЗКИ: кандидат по числу датчиков vs поведение ===")
    verdicts = {}
    for coll in sorted(collectors, key=lambda c: -gas_by_coll[c]):
        cs = cand[coll]
        days = sig_days.get(coll, set())
        hits = []
        for o in cs:
            dm = ppr[o]["этапы"].get(STAGE_DEMONT)
            if dm is not None and any(abs(dd - dm) <= MATCH_TOL for dd in days):
                hits.append(o)
        testable = [o for o in cs if ppr[o]["этапы"].get(STAGE_DEMONT, 10 ** 9) <= end_ord]
        if not cs:
            v = "нет кандидата по числу датчиков"
        elif len(hits) == 1:
            v = (f"ПОДТВЕРЖДЕНО -> {hits[0]}"
                 + (f" (разведено из {len(cs)} кандидатов)" if len(cs) > 1 else ""))
        elif len(hits) > 1:
            v = f"двойное: подпись совпала с {', '.join(hits)}"
        elif testable:
            rest = [o for o in cs if o not in testable]
            v = f"не подтверждено (подписей у демонтажа {', '.join(testable)} нет)"
            if rest:
                v += f"; остаются {', '.join(rest)} (позже июня, не проверяемы)"
        else:
            v = "не проверяемо (все кандидаты позже июня, вне журнала)"
        verdicts[coll] = (v, hits, cs)
        ev_txt = ", ".join(datetime.date.fromordinal(x).isoformat() for x in sorted(days)) or "—"
        print(f"  {collectors[coll]:<22} газ {gas_by_coll[coll]:>3}  подписи: {ev_txt}")
        print(f"  {'':<22} кандидаты: {', '.join(cs) or '—'}  =>  {v}")

    claims = defaultdict(list)
    for coll, (v, hits, cs) in verdicts.items():
        for o in hits:
            claims[o].append(coll)
    print("\n=== ОБРАТНЫЕ КОЛЛИЗИИ (объект ППР, подтверждённый несколькими коллекторами) ===")
    any_claim = False
    for o, cs in sorted(claims.items()):
        if len(cs) > 1:
            any_claim = True
            print(f"  {o}: " + ", ".join(collectors[c] for c in cs))
    if not any_claim:
        print("  нет")

    # ---------------- цена для метки ----------------
    def in_union(day):
        return any(ws <= day <= we for (o, pk, ws, we, s, e) in windows)

    own_window = {}
    for coll, (v, hits, cs) in verdicts.items():
        for o in hits:
            s = ppr[o]["этапы"].get(STAGE_DEMONT)
            e = ppr[o]["этапы"].get(STAGE_PRIEM)
            if s and e:
                own_window[coll] = (s - MATCH_TOL, e + MATCH_TOL, o)

    tot = defaultdict(int)
    for (day, node), r in rec.items():
        if node[0] != "C":
            continue
        coll = node[1]
        n_gas, n_all = cnt(r[I_NCH_G]), cnt(r[I_NCH])
        tot["ch_gas"] += n_gas
        tot["ch_all"] += n_all
        if in_union(day):
            tot["ch_gas_win"] += n_gas
            tot["ch_all_win"] += n_all
        mw = own_window.get(coll)
        if mw and mw[0] <= day <= mw[1]:
            tot["ch_gas_own"] += n_gas
            tot["ch_all_own"] += n_all

    sig_day_set = set()          # (коллектор, день) с подписью S1 или S2
    for ev in events:
        if ev["область"] == "газ" and ev["тип"] in ("всплеск Неиспр", "тишина"):
            if ev["тип"] == "тишина":
                for dd in range(ev["дата"], ev["дата"] + ev["эпизод_дней"]):
                    sig_day_set.add((ev["коллектор"], dd))
            else:
                sig_day_set.add((ev["коллектор"], ev["дата"]))
    day_union = sum(1 for (c, d) in sig_day_set if in_union(d))
    day_own = sum(1 for (c, d) in sig_day_set
                  if c in own_window and own_window[c][0] <= d <= own_window[c][1])

    win_days = sum(1 for d in all_days if in_union(d))
    n_sig = len([e for e in events if e["область"] == "газ"
                 and e["тип"] in ("всплеск Неиспр", "тишина")])
    n_sig_win = len([e for e in events if e["область"] == "газ"
                     and e["тип"] in ("всплеск Неиспр", "тишина") and e["окна_ППР"]])
    n_sig_dem = len([e for e in events if e["область"] == "газ"
                     and e["тип"] in ("всплеск Неиспр", "тишина") and e["совп_демонтаж"] != ""])
    n_ctrl = len([e for e in events if e["область"] == "все"
                  and e["тип"] in ("всплеск Неиспр", "тишина")])

    print("\n=== ЦЕНА ДЛЯ МЕТКИ (янв–июн 2026, наши 16 коллекторов) ===")
    print(f"  дней в журнале: {len(all_days)}, из них в каком-либо окне ППР ±2: {win_days}")
    print(f"  канало-дней «Неисправен» (газ) всего:                {tot['ch_gas']}")
    print(f"  ... в окнах ППР ±2 (любой объект):                   {tot['ch_gas_win']}")
    print(f"  ... в окне своего подтверждённого объекта:            {tot['ch_gas_own']}")
    print(f"  канало-дней «Неисправен» (все каналы) всего:          {tot['ch_all']}")
    print(f"  ... в окнах ППР ±2 (любой объект):                   {tot['ch_all_win']}")
    print(f"  ... в окне своего подтверждённого объекта:            {tot['ch_all_own']}")
    print(f"  дней-подписей (коллектор × день, S1/S2, газ):        {len(sig_day_set)}")
    print(f"  ... в окнах ППР ±2 (любой объект):                   {day_union}")
    print(f"  ... в окне своего подтверждённого объекта:            {day_own}")
    print(f"  событий-подписей (газ, S1+S2):                       {n_sig}")
    print(f"  ... в каком-либо окне ППР:                           {n_sig_win}")
    print(f"  ... в ±2 от любого демонтажа:                        {n_sig_dem}")
    print(f"  контроль: событий по всем каналам (S1+S2):           {n_ctrl}")

    print("\n=== ЛОЖНЫЕ СРАБАТЫВАНИЯ (подписи газа вне всех окон ППР ±2) ===")
    fp = [e for e in events if e["область"] == "газ"
          and e["тип"] in ("всплеск Неиспр", "тишина") and not e["окна_ППР"]]
    for ev in sorted(fp, key=lambda e: e["дата"]):
        print(f"  {datetime.date.fromordinal(ev['дата']).isoformat()}  "
              f"{collectors[ev['коллектор']]:<22} {ev['тип']:<15} "
              f"кан_Неиспр {ev['каналов_Неиспр']:>3}  эпизод {ev['эпизод_дней']:>2} дн")
    if not fp:
        print("  нет")

    print("\n=== ПОДПИСИ ГАЗА, НЕ СОВПАВШИЕ НИ С ОДНИМ ДЕМОНТАЖОМ (±2) ===")
    for ev in sorted(events, key=lambda e: e["дата"]):
        if ev["область"] == "газ" and ev["тип"] in ("всплеск Неиспр", "тишина") \
                and ev["совп_демонтаж"] == "":
            print(f"  {datetime.date.fromordinal(ev['дата']).isoformat()}  "
                  f"{collectors[ev['коллектор']]:<22} {ev['тип']:<15} "
                  f"кан_Неиспр {ev['каналов_Неиспр']:>3}  ближайший_этап: "
                  f"{ev['этап'] or 'нет'} {ev['объект_ППР'] or ''}")

    # ---------------- события в CSV ----------------
    with open(EVENTS_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["дата", "коллектор", "область", "тип_события", "активных_каналов",
                    "медиана_месяца", "каналов_Неисправен", "строк_Неисправен",
                    "эпизод_дней", "ближайший_этап", "дата_этапа", "объект_ППР",
                    "пакет", "датчиков_в_объекте", "сдвиг_дней",
                    "совп_демонтаж_дней", "совп_вывоз_дней", "окна_ППР",
                    "артефакт_журнала"])
        for ev in sorted(events, key=lambda e: (e["дата"], e["коллектор"],
                                                e["область"], e["тип"])):
            w.writerow([datetime.date.fromordinal(ev["дата"]).isoformat(),
                        collectors[ev["коллектор"]], ev["область"], ev["тип"],
                        ev["активных"], ev["медиана"], ev["каналов_Неиспр"],
                        ev["строк_Неиспр"], ev["эпизод_дней"],
                        ev["этап"],
                        (datetime.date.fromordinal(ev["дата_этапа"]).isoformat()
                         if ev["дата_этапа"] else ""),
                        ev["объект_ППР"], ev["пакет"], ev["датчиков_в_объекте"],
                        ev["сдвиг_дней"], ev["совп_демонтаж"], ev["совп_вывоз"],
                        ev["окна_ППР"], ev["артефакт_журнала"]])

    print("\nГОТОВО. Выгрузки: exp_ppr_signatures.csv, exp_ppr_signatures_daily.csv")


if __name__ == "__main__":
    main()
