#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эксперимент MOS-225: «пачки» превышений 1 % метана — плановая поверка газоанализаторов?

Гипотеза (открытая, из MOS-225): дни, когда метан ≥ 1 % показывают ~40 каналов
сразу, — это плановая проверка газоанализаторов поверочной смесью, а не утечки.
Если это проверка, такие дни нельзя считать событиями при обучении модели.

Что делает скрипт (stdlib, без pandas; журналы стримятся построчно):
  1. Проход 1 по dataset/ext-journal-2025.csv и ext-journal-2026.csv:
     по каждому дню — сколько газовых каналов показало значение ≥ 1 %,
     сколько строк ≥ 1 %, сколько строк «Обнаружен газ», сколько каналов
     с «Неисправен», сколько тревожных строк; те же счётчики по всем каналам
     (для сверки с ранее посчитанными числами).
  2. Дни-пачки: газовых каналов ≥ 1 % — от BATCH_MIN_CHANNELS и выше.
     Плюс контрольные даты 12.05.2025 / 14.05.2025 / 30.10.2025.
  3. Проход 2 — детали по дням-пачкам: по каждому каналу число строк ≥ 1 %,
     максимум, первое/последнее время ≥ 1 %, строки «Обнаружен газ» /
     «Неисправен» / тревожные; почасовая гистограмма строк ≥ 1 %.
  4. Сопоставление с планом: месяцы ТО газоанализаторов (to_schedule.csv),
     окна ППР-пакетов (ppr_schedule.csv), сезонность, день недели.
  5. Выгрузка analysis/to_ppr/exp_batches_days.csv и сводки в stdout.

Запуск:  .venv/bin/python analysis/to_ppr/exp_batches.py
"""

import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, 'dataset')
JOURNALS = [
    os.path.join(DATA, 'ext-journal-2025.csv'),
    os.path.join(DATA, 'ext-journal-2026.csv'),
]
CHANNELS_CSV = os.path.join(DATA, 'справочник_каналов_датчиков.csv')
TO_CSV = os.path.join(HERE, 'to_schedule.csv')
PPR_CSV = os.path.join(HERE, 'ppr_schedule.csv')
OUT_DAYS_CSV = os.path.join(HERE, 'exp_batches_days.csv')
CACHE = os.path.join(HERE, '.exp_batches_pass1.json')

BATCH_MIN_CHANNELS = 10          # порог «пачки»: газовых каналов ≥ 1 % за день
THRESH = 1.0                     # порог тревоги заказчика: 1 % об. метана
KNOWN_DATES = ['2025-05-12', '2025-05-14', '2025-10-30']  # из for-ml-team.md
GAS_TYPE = 'Газовый датчик'
WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

# индексы счётчиков в записи дня (проход 1)
C_G_GE1, C_G_DET, C_G_FAULT, C_G_ALARM = 0, 1, 2, 3
C_A_GE1, C_A_DET, C_A_FAULT, C_A_ALARM = 4, 5, 6, 7


def load_gas_channels():
    """Множество ид_канала_данных газовых каналов (тип_датчика == «Газовый датчик»)."""
    ids = set()
    total = 0
    with open(CHANNELS_CSV, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            total += 1
            if row['тип_датчика'].strip() == GAS_TYPE:
                ids.add(int(row['ид_канала_данных']))
    return ids, total


def new_rec():
    # [глобальные множества каналов: g_ge1, g_fault, a_ge1, a_fault] + [8 счётчиков]
    return [set(), set(), set(), set(), [0] * 8]


def pass1(args):
    """Проход 1: по дням — счётчики событий (газовые каналы и все каналы)."""
    path, gas = args
    days = {}
    with open(path, encoding='utf-8', newline='') as f:
        rd = csv.reader(f)
        next(rd)  # шапка
        for row in rd:
            # в 2025 файле — склейка двух полугодовых выгрузок, посреди есть вторая шапка
            if not row[1][:1].isdigit():
                continue
            d = row[2]
            ch = int(row[1])
            alarm = row[4] == 't'
            val = row[5]
            c0 = val[:1]
            num = None
            if c0 <= '9' and (c0 >= '0' or c0 == '-' or c0 == ','):
                try:
                    num = float(val.replace(',', '.'))
                except ValueError:
                    num = None
            rec = days.get(d)
            if rec is None:
                rec = days[d] = new_rec()
            cnt = rec[4]
            is_gas = ch in gas
            if num is not None and num >= THRESH:
                if is_gas:
                    rec[0].add(ch)
                    cnt[C_G_GE1] += 1
                rec[2].add(ch)
                cnt[C_A_GE1] += 1
            elif val == 'Обнаружен газ':
                if is_gas:
                    cnt[C_G_DET] += 1
                cnt[C_A_DET] += 1
            elif val == 'Неисправен':
                if is_gas:
                    rec[1].add(ch)
                    cnt[C_G_FAULT] += 1
                rec[3].add(ch)
                cnt[C_A_FAULT] += 1
            if alarm:
                if is_gas:
                    cnt[C_G_ALARM] += 1
                cnt[C_A_ALARM] += 1
    out = {}
    for d, rec in days.items():
        out[d] = [len(rec[0]), len(rec[1]), len(rec[2]), len(rec[3])] + rec[4]
    return path, out


def pass2(args):
    """Проход 2: детали по выбранным дням (каналы, максимумы, тайминг, почасовая гистограмма)."""
    path, gas, days_wanted = args
    out = {}
    with open(path, encoding='utf-8', newline='') as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            d = row[2]
            if d not in days_wanted:
                continue
            if not row[1][:1].isdigit():
                continue
            day = out.get(d)
            if day is None:
                day = out[d] = {'ch': {}, 'hour': [0] * 24, 'bins': [0] * 8}
            ch = int(row[1])
            if ch not in gas:
                continue
            val = row[5]
            c0 = val[:1]
            num = None
            if c0 <= '9' and (c0 >= '0' or c0 == '-' or c0 == ','):
                try:
                    num = float(val.replace(',', '.'))
                except ValueError:
                    num = None
            if num is not None and num >= THRESH:
                t = row[3]
                c = day['ch'].get(ch)
                if c is None:
                    c = day['ch'][ch] = [0, num, t, t, 0, 0, 0]
                else:
                    if c[1] is None or num > c[1]:
                        c[1] = num
                    if not c[2] or t < c[2]:
                        c[2] = t
                    if not c[3] or t > c[3]:
                        c[3] = t
                c[0] += 1
                day['hour'][int(t[:2])] += 1
                # корзины значений: 1.0–1.1, 1.1–1.3, 1.3–1.5, 1.5–1.7, 1.7–2.0, 2–5, 5–15, ≥15
                bins = day['bins']
                if num < 1.1:
                    bins[0] += 1
                elif num < 1.3:
                    bins[1] += 1
                elif num < 1.5:
                    bins[2] += 1
                elif num < 1.7:
                    bins[3] += 1
                elif num < 2.0:
                    bins[4] += 1
                elif num < 5.0:
                    bins[5] += 1
                elif num < 15.0:
                    bins[6] += 1
                else:
                    bins[7] += 1
            elif val == 'Обнаружен газ':
                c = day['ch'].get(ch)
                if c is None:
                    c = day['ch'][ch] = [0, None, '', '', 0, 0, 0]
                c[4] += 1
            elif val == 'Неисправен':
                c = day['ch'].get(ch)
                if c is None:
                    c = day['ch'][ch] = [0, None, '', '', 0, 0, 0]
                c[5] += 1
            if row[4] == 't':
                c = day['ch'].get(ch)
                if c is None:
                    c = day['ch'][ch] = [0, None, '', '', 0, 0, 0]
                c[6] += 1
    return path, out


# ---------------------------------------------------------------- план

def load_to_months():
    """Месяцы 2026 с планом ТО/ТР газоанализаторов: {месяц: число отметок}."""
    marks = Counter()
    hidden = 0
    with open(TO_CSV, encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            if r['вид_оборудования'].strip() != 'Газоанализаторы':
                continue
            if not r['месяц_план']:
                hidden += 1
                continue
            if r['строка_скрыта'] == '1':
                hidden += 1
                continue
            marks[r['месяц_план']] += 1
    return marks, hidden


def load_ppr_windows():
    """Окна ППР-пакетов: {пакет: (начало, конец, {этап: дата})}."""
    pk = defaultdict(dict)
    with open(PPR_CSV, encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            pk[r['пакет_id']][r['этап']] = r['дата_этапа']
    windows = {}
    for p, stages in pk.items():
        ds = sorted(stages.values())
        windows[p] = (ds[0], ds[-1], stages)
    return windows


def ppr_match(day_iso, windows):
    """(окно-пакет или '', (ближайший этап, дней до него)) для даты."""
    d = date.fromisoformat(day_iso)
    in_pkg = ''
    best = None
    for p, (start, end, stages) in windows.items():
        if start <= day_iso <= end:
            in_pkg = p
        for s in stages.values():
            delta = abs((d - date.fromisoformat(s)).days)
            if best is None or delta < best[1]:
                best = (s, delta)
    return in_pkg, best


# ---------------------------------------------------------------- отчёт

def fmt_table(header, rows):
    w = [len(h) for h in header]
    for r in rows:
        for i, v in enumerate(r):
            w[i] = max(w[i], len(str(v)))
    line = ' | '.join(h.ljust(w[i]) for i, h in enumerate(header))
    sep = '-+-'.join('-' * w[i] for i in range(len(header)))
    body = '\n'.join(' | '.join(str(v).ljust(w[i]) for i, v in enumerate(r)) for r in rows)
    return line + '\n' + sep + '\n' + body


def main():
    gas, total_channels = load_gas_channels()
    print(f'Справочник каналов: {total_channels} всего, газовых («{GAS_TYPE}»): {len(gas)}')

    # ---- проход 1 (параллельно по двум журналам) ----
    if os.path.exists(CACHE):
        with open(CACHE, encoding='utf-8') as f:
            days = json.load(f)
        print(f'Проход 1: взят кэш {CACHE} ({len(days)} дней)')
    else:
        with Pool(2) as pool:
            results = pool.map(pass1, [(p, gas) for p in JOURNALS])
        days = {}
        for path, part in results:
            overlap = set(days) & set(part)
            if overlap:
                print(f'ВНИМАНИЕ: даты пересекаются между журналами: {sorted(overlap)[:5]}')
            days.update(part)
        with open(CACHE, 'w', encoding='utf-8') as f:
            json.dump(days, f)
        print(f'Проход 1: {len(days)} дней с событиями')

    def cnt(d, i):
        return days[d][4 + i]

    def gch(d, i):
        return days[d][i]

    # ---- кросс-проверки с ранее посчитанными числами ----
    print('\n=== Кросс-проверки (известные числа из docs/for-ml-team.md) ===')
    y25_ge1 = sum(cnt(d, C_G_GE1) for d in days if d.startswith('2025'))
    y25_det = sum(cnt(d, C_G_DET) for d in days if d.startswith('2025'))
    print(f'2025, газовые каналы: строк ≥ 1 % — {y25_ge1} (ожидалось 12 617), '
          f'строк «Обнаружен газ» — {y25_det} (ожидалось 2 712)')
    for d in KNOWN_DATES:
        if d in days:
            print(f'{d}: газовых каналов ≥ 1 % — {gch(d, 0)}, строк ≥ 1 % — {cnt(d, C_G_GE1)}; '
                  f'все каналы: {gch(d, 2)}, строк — {cnt(d, C_A_GE1)}; '
                  f'«Обнаружен газ» — {cnt(d, C_G_DET)}, «Неисправен» строк — {cnt(d, C_G_FAULT)}, '
                  f'тревожных — {cnt(d, C_G_ALARM)}')
        else:
            print(f'{d}: НЕ НАЙДЕН в журнале')

    # ---- дни-пачки ----
    batch = sorted(d for d in days if gch(d, 0) >= BATCH_MIN_CHANNELS)
    batch = [d for d in batch if d[:4] in ('2025', '2026')]
    print(f'\n=== Дни-пачки (газовых каналов ≥ 1 % — от {BATCH_MIN_CHANNELS}) ===')
    print(f'Всего дней-пачек: {len(batch)}; из них 2025: {sum(1 for d in batch if d[:4] == "2025")}, '
          f'2026: {sum(1 for d in batch if d[:4] == "2026")}')

    # распределение по числу каналов (все дни 2025–2026)
    buckets = Counter()
    for d in days:
        if d[:4] not in ('2025', '2026'):
            continue
        n = gch(d, 0)
        if n == 0:
            buckets['0'] += 1
        elif n < 5:
            buckets['1–4'] += 1
        elif n < 10:
            buckets['5–9'] += 1
        elif n < 20:
            buckets['10–19'] += 1
        elif n < 40:
            buckets['20–39'] += 1
        else:
            buckets['40+'] += 1
    print('Распределение дней 2025–2026 по числу газовых каналов ≥ 1 %: '
          + ', '.join(f'{k}: {buckets[k]}' for k in ['0', '1–4', '5–9', '10–19', '20–39', '40+']))

    # ---- проход 2: детали по дням-пачкам ----
    detail_days = set(batch) | set(KNOWN_DATES)
    with Pool(2) as pool:
        results = pool.map(pass2, [(p, gas, detail_days) for p in JOURNALS])
    details = {}
    for path, part in results:
        for d, day in part.items():
            if d not in details:
                details[d] = day
            else:
                cur = details[d]
                for ch, c in day['ch'].items():
                    k = cur['ch'].get(ch)
                    if k is None:
                        cur['ch'][ch] = c
                    else:
                        k[0] += c[0]
                        if c[1] is not None and (k[1] is None or c[1] > k[1]):
                            k[1] = c[1]
                        if c[2] and (not k[2] or c[2] < k[2]):
                            k[2] = c[2]
                        if c[3] and (not k[3] or c[3] > k[3]):
                            k[3] = c[3]
                        k[4] += c[4]
                        k[5] += c[5]
                        k[6] += c[6]
                for i in range(24):
                    cur['hour'][i] += day['hour'][i]
                for i in range(8):
                    cur['bins'][i] += day['bins'][i]

    # ---- сопоставление с планом ----
    to_marks, to_hidden = load_to_months()
    windows = load_ppr_windows()
    print('\n=== План ===')
    print('ТО/ТР газоанализаторов 2026 (to_schedule.csv), отметок по месяцам: '
          + ', '.join(f'{m[-2:]}: {to_marks[m]}' for m in sorted(to_marks))
          + f'; строк без месяца/скрытых: {to_hidden}')
    print('Окна ППР-пакетов 2026 (ppr_schedule.csv): '
          + '; '.join(f'{p} {w[0][5:]}…{w[1][5:]}' for p, w in sorted(windows.items())))

    # ---- таблица дней-пачек ----
    rows = []
    for d in batch:
        det = details.get(d, {'ch': {}, 'hour': [0] * 24})
        chs = {ch: c for ch, c in det['ch'].items() if c[0] > 0}
        maxes = sorted(c[1] for c in chs.values() if c[1] is not None)
        med_max = maxes[len(maxes) // 2] if maxes else ''
        hi5 = sum(1 for m in maxes if m >= 5.0)
        firsts = sorted(c[2] for c in chs.values() if c[2])
        lasts = sorted(c[3] for c in chs.values() if c[3])
        hour = det['hour']
        peak_hour = max(range(24), key=lambda i: hour[i]) if sum(hour) else -1
        bins = det['bins']
        d_date = date.fromisoformat(d)
        month_key = d[:7]
        to_flag = ('-' if d[:4] != '2026'
                   else (f'да ({to_marks.get(month_key, 0)} отметок)' if to_marks.get(month_key) else 'нет'))
        in_pkg, nearest = ppr_match(d, windows) if d[:4] == '2026' else ('-', (None, None))
        rows.append([
            d,
            WEEKDAYS[d_date.weekday()],
            gch(d, 0),
            cnt(d, C_G_GE1),
            cnt(d, C_G_DET),
            cnt(d, C_G_FAULT),
            gch(d, 1),
            cnt(d, C_G_ALARM),
            gch(d, 2),
            cnt(d, C_A_GE1),
            f'{med_max:.2f}' if med_max != '' else '',
            hi5,
            bins[0] + bins[1],
            bins[4] + bins[5] + bins[6] + bins[7],
            firsts[0] if firsts else '',
            lasts[-1] if lasts else '',
            peak_hour if peak_hour >= 0 else '',
            to_flag,
            in_pkg if in_pkg else 'вне окна',
            f'{nearest[0]} ({nearest[1]} дн.)' if nearest and nearest[0] else '',
        ])

    header = ['дата', 'день_недели', 'газ_каналов_ge1', 'строк_ge1', 'строк_обнаружен_газ',
              'строк_неисправен', 'каналов_неисправен', 'строк_тревожных',
              'всех_каналов_ge1', 'всех_строк_ge1', 'медиана_максимума', 'каналов_макс_ge5',
              'строк_1_0_1_3', 'строк_выше_2',
              'первое_ge1', 'последнее_ge1', 'пик_час',
              'месяц_в_плане_ТО', 'окно_ППР', 'ближайший_этап_ППР']
    with open(OUT_DAYS_CSV, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f'\nЗаписано: {OUT_DAYS_CSV} ({len(rows)} строк)')

    print('\n=== Таблица дней-пачек ===')
    print(fmt_table(header, rows))

    # ---- сезонность ----
    print('\n=== Сезонность дней-пачек (число дней по месяцам) ===')
    for year in ('2025', '2026'):
        mc = Counter(int(d[5:7]) for d in batch if d[:4] == year)
        print(f'{year}: ' + ', '.join(f'{m:02d}:{mc.get(m, 0)}' for m in range(1, 13)))
    wdc = Counter(WEEKDAYS[date.fromisoformat(d).weekday()] for d in batch)
    print('По дням недели: ' + ', '.join(f'{k}:{wdc[k]}' for k in WEEKDAYS))

    # ---- базовые доли: насколько «случайно» совпадали бы дни с планом ----
    print('\n=== Базовые доли для оценки совпадений с планом ===')
    from datetime import timedelta
    jdays = [date(2026, 1, 1) + timedelta(i) for i in range(181)]  # янв–июн 2026
    all_stages = [s for w in windows.values() for s in w[2].values()]
    vyvoz = [w[2]['вывоз_датчиков_из_ОМ'] for w in windows.values()]

    def share_near(stages, k):
        ss = [date.fromisoformat(s) for s in stages]
        return sum(1 for d in jdays if any(abs((d - s).days) <= k for s in ss)) / len(jdays)

    in_win = sum(1 for d in jdays
                 if any(w[0] <= d.isoformat() <= w[1] for w in windows.values())) / len(jdays)
    print(f'Янв–июн 2026: доля дней внутри окна ППР — {in_win:.0%}; '
          f'±0/±1/±2 дн. от любого этапа — {share_near(all_stages, 0):.0%}/'
          f'{share_near(all_stages, 1):.0%}/{share_near(all_stages, 2):.0%}; '
          f'от «вывоз из ОМ» — {share_near(vyvoz, 0):.0%}/'
          f'{share_near(vyvoz, 1):.0%}/{share_near(vyvoz, 2):.0%}')
    b26 = [d for d in batch if d[:4] == '2026']
    near1 = sum(1 for d in b26 if any(abs((date.fromisoformat(d) - date.fromisoformat(s)).days) <= 1
                                      for s in vyvoz))
    near1_any = sum(1 for d in b26 if any(abs((date.fromisoformat(d) - date.fromisoformat(s)).days) <= 1
                                          for s in all_stages))
    print(f'Наблюдается: {near1}/{len(b26)} дней-пачек 2026 в пределах ±1 дн. от «вывоза из ОМ» '
          f'(база {share_near(vyvoz, 1):.0%}), {near1_any}/{len(b26)} — от любого этапа '
          f'(база {share_near(all_stages, 1):.0%})')
    n = len(batch)
    print(f'Все {n} дней-пачек — будни: вероятность при случайном выборе (5/7)^{n} = '
          f'{(5 / 7) ** n:.2e}')
    active = [d for d in days if d[:4] in ('2025', '2026') and cnt(d, C_G_GE1) > 0]
    act_wd = sum(1 for d in active if date.fromisoformat(d).weekday() < 5)
    print(f'Для сравнения: из {len(active)} дней 2025–2026 с любыми строками ≥ 1 % '
          f'будни — {act_wd} ({act_wd / len(active):.0%})')

    # ---- совпадение каналов между днями-пачками ----
    print('\n=== Совпадение газовых каналов между днями-пачками (Jaccard) ===')
    bsets = {d: {ch for ch, c in details[d]['ch'].items() if c[0] > 0} for d in batch if d in details}
    pairs = []
    for i, d1 in enumerate(batch):
        for d2 in batch[i + 1:]:
            if d1 not in bsets or d2 not in bsets:
                continue
            a, b = bsets[d1], bsets[d2]
            inter = len(a & b)
            union = len(a | b)
            pairs.append((d1, d2, inter, round(inter / union, 2) if union else 0))
    for d1, d2, inter, j in pairs:
        print(f'{d1} × {d2}: общих {inter}, Jaccard {j}')
    # сколько каналов участвует в нескольких пачках
    freq = Counter()
    for s in bsets.values():
        for ch in s:
            freq[ch] += 1
    multi = sum(1 for v in freq.values() if v > 1)
    print(f'Уникальных газовых каналов в пачках: {len(freq)}; из них в нескольких пачках: {multi}')

    # ---- профиль значений и времени (альтернатива: реальное событие) ----
    print('\n=== Профиль пачек: значения и тайминг ===')
    for d in batch:
        if d not in details:
            continue
        chs = [c for c in details[d]['ch'].values() if c[0] > 0]
        maxes = [c[1] for c in chs if c[1] is not None]
        b = Counter()
        for m in maxes:
            if m < 2:
                b['1–2'] += 1
            elif m < 5:
                b['2–5'] += 1
            elif m < 15:
                b['5–15'] += 1
            else:
                b['15+'] += 1
        med = sorted(maxes)[len(maxes) // 2] if maxes else 0
        span_min = min((c[2] for c in chs if c[2]), default='')
        span_max = max((c[3] for c in chs if c[3]), default='')
        det = sum(c[4] for c in chs)
        fault = sum(c[5] for c in chs)
        al = sum(c[6] for c in chs)
        hour = details[d]['hour']
        hist = ','.join(f'{i:02d}={hour[i]}' for i in range(24) if hour[i])
        print(f'{d}: каналов {len(chs)}, медиана максимума {med:.2f} %, '
              f'максимумы каналов 1–2%:{b["1–2"]} 2–5%:{b["2–5"]} 5–15%:{b["5–15"]} 15+%:{b["15+"]}; '
              f'время ≥1 % — {span_min}…{span_max}; «Обнаружен газ» {det}, «Неисправен» {fault}, '
              f'тревожных {al}\n    часы (строк ≥1 %): {hist}')
        bn = details[d]['bins']
        print('    строки по значениям: '
              + ', '.join(f'{lab}:{bn[i]}' for i, lab in enumerate(
                  ['1.0–1.1', '1.1–1.3', '1.3–1.5', '1.5–1.7', '1.7–2.0', '2–5', '5–15', '15+'])))

    # ---- фон: «Обнаружен газ» и тревоги вне пачек ----
    print('\n=== Фон: строки «Обнаружен газ» и тревожные (2025–2026) ===')
    for year in ('2025', '2026'):
        ydays = [d for d in days if d.startswith(year)]
        bdays = [d for d in batch if d.startswith(year)]
        det_all = sum(cnt(d, C_G_DET) for d in ydays)
        det_b = sum(cnt(d, C_G_DET) for d in bdays)
        al_all = sum(cnt(d, C_G_ALARM) for d in ydays)
        al_b = sum(cnt(d, C_G_ALARM) for d in bdays)
        n = 366 if year == '2024' else (365 if year == '2025' else 181)
        print(f'{year}: «Обнаружен газ» всего {det_all}, из них в пачках {det_b} '
              f'(средняя/день вне пачек {(det_all - det_b) / max(1, n - len(bdays)):.2f}); '
              f'тревожных всего {al_all}, в пачках {al_b} '
              f'(средняя/день вне пачек {(al_all - al_b) / max(1, n - len(bdays)):.2f})')

    if os.path.exists(CACHE):
        os.remove(CACHE)
    print('\nГотово.')


if __name__ == '__main__':
    sys.exit(main())
