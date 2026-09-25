#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эксперимент: видна ли плановая работа (ТО/ППР) в журнале СМВУ 2026?

Модель «естественного эксперимента»: коллектор Ясенево (ТО-файл, объект № 9,
«к-р Ясенево») — обрабатываемая группа (ТО ГАСБ: фев, авг, ноя; ТО+ТР ГАСБ: май),
контроль — газовые каналы остальных коллекторов (план ТО на них в мае у других
объектов свой, но это и есть «фон»: если всплеск везде — подпись ТО это не является).

Привязка: справочник каналов, строки, где тег/имя содержит «Ясенево»
(914-g18. «ДП Ясенево ОС» и 914-g19. «к-р Ясенево») → префиксы 914/915
(семейство «объект Мю» по дереву объектов), 1487 каналов. Газовых каналов в
914/915 ровно 40 — совпадает с «ГАСБ 40 шт.» графика ТО объекта 9.

Группы:
  yas_gas   — 40 газовых каналов 914/915 (ГАСБ к-р Ясенево)  — ГЛАВНАЯ
  yas_all   — все 1487 каналов 914/915                        — широкий охват
  ctrl_gas  — 489 газовых каналов прочих префиксов            — КОНТРОЛЬ (та же техника)
  all_gas   — все 529 газовых канала парка
  park_all  — все каналы справочника (фон парка)

Метрики (месяц × группа):
  строк записей; активных каналов; каналов без строк за месяц;
  строк «Неисправен» и каналов с ними; строки прочих текстовых состояний;
  строк «Обнаружен газ»; строк с числом >= 1 % об. CH4;
  каналов с тишиной > 24 ч подряд (пропуск записей между двумя соседними
  записями канала) и числа таких пропусков; максимальный пропуск, ч;
  активных дней на канал (медиана).

Плюс выгрузки: помесячно (exp_yasenevo_monthly.csv), текстовые состояния
(exp_yasenevo_states.csv), по дням (exp_yasenevo_daily.csv — окна ППР помечены),
по каналам ГАСБ Ясенево (exp_yasenevo_channels.csv).

Запуск:  .venv/bin/python analysis/to_ppr/exp_yasenevo.py
Стриминговый разбор stdlib (csv.reader), pandas нет, всё в память не грузится.
"""

import csv
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAN_REF = os.path.join(ROOT, "dataset", "справочник_каналов_датчиков.csv")
JOURNAL = os.path.join(ROOT, "dataset", "ext-journal-2026.csv")
TO_CSV = os.path.join(ROOT, "analysis", "to_ppr", "to_schedule.csv")
PPR_CSV = os.path.join(ROOT, "analysis", "to_ppr", "ppr_schedule.csv")
OUT = os.path.join(ROOT, "analysis", "to_ppr")

SILENCE_S = 24 * 3600          # тишина > 24 ч подряд
STATE_NEISPRAVEN = "Неисправен"
STATE_GAS = "Обнаружен газ"
GE1 = 1.0                      # >= 1 % об. CH4

# индексы счётчиков в записи (канал, месяц)
C_ROWS, C_NUM, C_GE1, C_TEXT, C_NEISP, C_GASDET, C_GAPN, C_GAPMAX, C_FIRST, C_LAST = range(10)


def load_channels():
    """ид_канала -> (префикс, флаг_газ, имя, ид_объект). Плюс проверка привязки Ясенево."""
    chans = {}
    yas_name_hits = []
    with open(CHAN_REF, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cid = int(r["ид_канала_данных"])
            tag = r["тег_инженерной_системы"]
            pref = tag.split("-")[0]
            is_gas = (r["тип_датчика"] == "Газовый датчик") or (r["тип_инж_системы"] == "Газовая охрана")
            name = r["название_датчика"]
            chans[cid] = (pref, is_gas, name, int(r["ид_объект"]), tag)
            if "Ясенево" in tag or "Ясенево" in name:
                yas_name_hits.append((cid, tag, name, r["ид_объект"], pref))
    return chans, yas_name_hits


def load_plan():
    """План работ: ТО/ТР объекта 9 (Ясенево) и окна ППР, попадающие в янв–июн 2026."""
    to_yas = []
    with open(TO_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["объект_номер"] == "9":
                to_yas.append((r["вид_оборудования"], r["количество"], r["вид_работ"],
                               r["месяц_план"], r["строка_скрыта"]))
    ppr = defaultdict(dict)  # пакет -> {этап: дата}
    ppr_qty = {}
    with open(PPR_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["дата_этапа"]:
                ppr[r["пакет_id"]][r["этап"]] = r["дата_этапа"]
                ppr_qty[r["пакет_id"]] = int(r["кол_датчиков_шт"])
    windows = []
    for pid, stages in sorted(ppr.items()):
        start = stages.get("начало_демонтажа_датчиков", "")
        end = stages.get("сдача_работ_комиссии", "")
        if start and start <= "2026-06-30":
            windows.append((pid, start, end, ppr_qty.get(pid, 0)))
    return to_yas, windows


def main():
    chans, yas_name_hits = load_channels()
    to_yas, ppr_windows = load_plan()

    yas_ids = {c for c, v in chans.items() if v[0] in ("914", "915")}
    gas_ids = {c for c, v in chans.items() if v[1]}
    yas_gas = yas_ids & gas_ids
    ctrl_gas = gas_ids - yas_ids
    groups = {
        "yas_gas": yas_gas,
        "yas_all": yas_ids,
        "ctrl_gas": ctrl_gas,
        "all_gas": gas_ids,
        "park_all": set(chans),
    }

    print("=== ПРИВЯЗКА ЯСЕНЕВО ===")
    print("строк справочника с «Ясенево» в теге/имени:")
    for cid, tag, name, oid, pref in yas_name_hits:
        print(f"  ид={cid} тег={tag!r} имя={name!r} ид_объект={oid} префикс={pref}")
    print(f"префиксы 914/915: {len(yas_ids)} каналов; газовых среди них: {len(yas_gas)}")
    print(f"газовых всего: {len(gas_ids)}; контроль (газ прочих префиксов): {len(ctrl_gas)}")
    print(f"доля 914/915 в справочнике: {100.0*len(yas_ids)/len(chans):.1f} %")
    print("\n=== ПЛАН: объект 9 «к-р Ясенево» (to_schedule.csv) ===")
    for row in to_yas:
        print("  ", row)
    print("\n=== ПЛАН: ППР-пакеты с датами в янв–июн 2026 (ppr_schedule.csv) ===")
    for pid, start, end, qty in ppr_windows:
        print(f"  {pid}: {start} … {end}, {qty} датчиков")

    # ---- стриминг журнала ----
    rec = {}                       # (канал, месяц) -> список счётчиков
    state_cnt = defaultdict(int)   # (канал, месяц, состояние) -> строк
    day_rows = defaultdict(int)    # (день_ord, группа) -> строк
    day_act = defaultdict(set)     # (день_ord, группа) -> каналы
    day_neisp = defaultdict(int)
    day_gasdet = defaultdict(int)
    day_ge1 = defaultdict(int)
    last_ts = {}                   # канал -> последняя метка времени (ord*86400+сек)
    date_cache = {}
    time_cache = {}
    inversions_ch = 0              # внутри канала ts убывает (нарушение порядка)
    inversions_glob = 0
    prev_ts = -1
    unknown_rows = 0
    unknown_ids = set()
    n_rows = 0
    months_seen = set()

    def parse_date(s):
        v = date_cache.get(s)
        if v is None:
            y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            # ordinal без datetime: кэш по строке даты (их < 400)
            import datetime
            v = (f"{y:04d}-{m:02d}", datetime.date(y, m, d).toordinal())
            date_cache[s] = v
        return v

    def parse_time(s):
        v = time_cache.get(s)
        if v is None:
            v = int(s[0:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])
            time_cache[s] = v
        return v

    # дамп точечных событий по каналам Ясенево: «Неисправен», «Обнаружен газ», >= 1 %
    # (с временем — чтобы различать синхронное штатное отключение и единичный отказ)
    ev_f = open(os.path.join(OUT, "exp_yasenevo_events.csv"), "w", encoding="utf-8", newline="")
    ev_out = csv.writer(ev_f)
    ev_out.writerow(["ид_события", "ид_канала", "имя_канала", "префикс",
                     "дата", "время", "тревожное", "значение"])

    with open(JOURNAL, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        print("\nшапка журнала:", header)
        for row in rd:
            n_rows += 1
            try:
                cid = int(row[1])
            except (ValueError, IndexError):
                unknown_rows += 1
                continue
            month, d_ord = parse_date(row[2])
            ts = d_ord * 86400 + parse_time(row[3])
            val = row[5]
            months_seen.add(month)

            if ts < prev_ts:
                inversions_glob += 1
            prev_ts = ts

            last = last_ts.get(cid)
            gap = 0
            if last is not None:
                if ts < last:
                    inversions_ch += 1
                    gap = 0            # строка вне порядка — пропуск не считаем
                else:
                    gap = ts - last
                    last_ts[cid] = ts  # last_ts двигается только вперёд
            else:
                last_ts[cid] = ts

            key = (cid, month)
            r = rec.get(key)
            if r is None:
                r = [0] * 10
                rec[key] = r
            r[C_ROWS] += 1
            if gap > SILENCE_S:
                r[C_GAPN] += 1
                if gap > r[C_GAPMAX]:
                    r[C_GAPMAX] = gap
            if r[C_FIRST] == 0:
                r[C_FIRST] = ts
            r[C_LAST] = ts

            try:
                x = float(val)
            except ValueError:
                x = None
            if x is None:
                r[C_TEXT] += 1
                state_cnt[(cid, month, val)] += 1
                if cid in yas_ids and val in (STATE_NEISPRAVEN, STATE_GAS) and ev_out:
                    ev_out.writerow([row[0], cid, chans[cid][2], chans[cid][0],
                                     row[2], row[3], row[4], val])
                if val == STATE_NEISPRAVEN:
                    r[C_NEISP] += 1
                if val == STATE_GAS:
                    r[C_GASDET] += 1
            else:
                r[C_NUM] += 1
                if x >= GE1:
                    r[C_GE1] += 1
                    if cid in yas_ids and ev_out:
                        ev_out.writerow([row[0], cid, chans[cid][2], chans[cid][0],
                                         row[2], row[3], row[4], val])

            if cid not in chans:
                unknown_rows += 1
                unknown_ids.add(cid)

            # дневные ряды — только по нашим группам (сокращает память)
            if cid in yas_gas:
                gs = ("yas_gas", "all_gas", "yas_all")
            elif cid in yas_ids:
                gs = ("yas_all",)
            elif cid in ctrl_gas:
                gs = ("ctrl_gas", "all_gas")
            else:
                gs = ("park_all",) if cid in chans else ()
            for g in gs:
                day_rows[(d_ord, g)] += 1
                day_act[(d_ord, g)].add(cid)
                if x is None:
                    if val == STATE_NEISPRAVEN:
                        day_neisp[(d_ord, g)] += 1
                    elif val == STATE_GAS:
                        day_gasdet[(d_ord, g)] += 1
                elif x >= GE1:
                    day_ge1[(d_ord, g)] += 1

            if n_rows % 5_000_000 == 0:
                print(f"... {n_rows/1e6:.0f} млн строк, {month}", flush=True)

    ev_f.close()
    print(f"\nстрок журнала: {n_rows:,}".replace(",", " "))
    print(f"месяцы: {sorted(months_seen)}")
    print(f"инверсии времени: глобально {inversions_glob}, внутри канала {inversions_ch}")
    print(f"строк с каналами вне справочника: {unknown_rows}, таких каналов: {len(unknown_ids)}")

    # ---- агрегация месяц × группа ----
    months = sorted(months_seen)
    rows_out = []
    for gname, gset in groups.items():
        for month in months:
            agg = dict(rows=0, active=set(), text=0, neisp_rows=0, neisp_ch=set(),
                       gasdet=0, ge1=0, num=0, gap_ch=set(), gap_n=0, gap_max=0,
                       days=[])
            for (cid, m), r in rec.items():
                if m != month or cid not in gset:
                    continue
                agg["rows"] += r[C_ROWS]
                agg["active"].add(cid)
                agg["num"] += r[C_NUM]
                agg["ge1"] += r[C_GE1]
                agg["text"] += r[C_TEXT]
                agg["neisp_rows"] += r[C_NEISP]
                if r[C_NEISP]:
                    agg["neisp_ch"].add(cid)
                agg["gasdet"] += r[C_GASDET]
                if r[C_GAPN]:
                    agg["gap_ch"].add(cid)
                    agg["gap_n"] += r[C_GAPN]
                    agg["gap_max"] = max(agg["gap_max"], r[C_GAPMAX])
            rows_out.append({
                "месяц": month, "группа": gname, "каналов_в_группе": len(gset),
                "строк": agg["rows"], "активных_каналов": len(agg["active"]),
                "каналов_без_строк": len(gset) - len(agg["active"]),
                "строк_числовых": agg["num"], "строк_текстовых": agg["text"],
                "строк_Неисправен": agg["neisp_rows"],
                "каналов_с_Неисправен": len(agg["neisp_ch"]),
                "строк_Обнаружен_газ": agg["gasdet"],
                "строк_ge_1pct": agg["ge1"],
                "каналов_с_тишиной_gt24ч": len(agg["gap_ch"]),
                "пропусков_gt24ч": agg["gap_n"],
                "макс_пропуск_ч": round(agg["gap_max"] / 3600.0, 1),
            })

    with open(os.path.join(OUT, "exp_yasenevo_monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    # ---- текстовые состояния: месяц × группа × состояние ----
    st_out = defaultdict(lambda: [0, set()])
    g_of = {}
    for gname, gset in groups.items():
        for cid in gset:
            g_of.setdefault(cid, []).append(gname)
    for (cid, month, state), cnt in state_cnt.items():
        for gname in g_of.get(cid, ["<вне справочника>"]):
            v = st_out[(month, gname, state)]
            v[0] += cnt
            v[1].add(cid)
    with open(os.path.join(OUT, "exp_yasenevo_states.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["месяц", "группа", "состояние", "строк", "каналов"])
        for (month, gname, state), (cnt, chset) in sorted(st_out.items()):
            w.writerow([month, gname, state, cnt, len(chset)])

    # ---- дневные ряды ----
    import datetime
    ppr_marks = {}
    for pid, start, end, qty in ppr_windows:
        d0 = datetime.date.fromisoformat(start).toordinal()
        d1 = datetime.date.fromisoformat(end).toordinal()
        for d in range(d0, d1 + 1):
            ppr_marks[d] = pid
    all_days = sorted({k[0] for k in day_rows})
    with open(os.path.join(OUT, "exp_yasenevo_daily.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["дата", "группа", "строк", "активных_каналов", "строк_Неисправен",
                    "строк_Обнаружен_газ", "строк_ge_1pct", "окно_ППР"])
        for d in all_days:
            ds = datetime.date.fromordinal(d).isoformat()
            for gname in groups:
                if (d, gname) not in day_rows:
                    continue
                w.writerow([ds, gname, day_rows[(d, gname)], len(day_act[(d, gname)]),
                            day_neisp.get((d, gname), 0), day_gasdet.get((d, gname), 0),
                            day_ge1.get((d, gname), 0), ppr_marks.get(d, "")])

    # ---- каналы ГАСБ Ясенево: месяц × канал ----
    with open(os.path.join(OUT, "exp_yasenevo_channels.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ид_канала", "имя", "тег", "месяц", "строк", "числовых", "текстовых",
                    "Неисправен", "Обнаружен_газ", "ge_1pct", "пропусков_gt24ч",
                    "макс_пропуск_ч"])
        for cid in sorted(yas_gas):
            _, _, name, _, tag = chans[cid]
            for month in months:
                r = rec.get((cid, month))
                if r is None:
                    w.writerow([cid, name, tag, month, 0, 0, 0, 0, 0, 0, 0, ""])
                else:
                    w.writerow([cid, name, tag, month, r[C_ROWS], r[C_NUM], r[C_TEXT],
                                r[C_NEISP], r[C_GASDET], r[C_GE1], r[C_GAPN],
                                round(r[C_GAPMAX] / 3600.0, 1)])

    # ---- сводка в stdout ----
    print("\n=== МЕСЯЦ × ГРУППА ===")
    hdr = ("месяц", "группа", "строк", "акт.кан", "без_строк", "Неисправен_строк",
           "Неисправен_кан", "Обн.газ", "ge1%", "тишина_кан", "пропусков", "макс_ч")
    print("{:<8} {:<9} {:>10} {:>7} {:>9} {:>8} {:>8} {:>7} {:>6} {:>8} {:>8} {:>8}".format(*hdr))
    for r in rows_out:
        print("{месяц:<8} {группа:<9} {строк:>10} {активных_каналов:>7} {каналов_без_строк:>9} "
              "{строк_Неисправен:>8} {каналов_с_Неисправен:>8} {строк_Обнаружен_газ:>7} "
              "{строк_ge_1pct:>6} {каналов_с_тишиной_gt24ч:>8} {пропусков_gt24ч:>8} "
              "{макс_пропуск_ч:>8}".format(**r))

    print("\n=== ТЕКСТОВЫЕ СОСТОЯНИЯ (все, парк целиком) ===")
    st_all = defaultdict(lambda: [0, set()])
    for (cid, month, state), cnt in state_cnt.items():
        v = st_all[(month, state)]
        v[0] += cnt
        v[1].add(cid)
    for (month, state), (cnt, chset) in sorted(st_all.items()):
        print(f"  {month}  {state!r:<24} строк {cnt:>9}  каналов {len(chset):>5}")

    print("\nГОТОВО. Выгрузки: exp_yasenevo_monthly.csv, exp_yasenevo_states.csv, "
          "exp_yasenevo_daily.csv, exp_yasenevo_channels.csv")


if __name__ == "__main__":
    main()