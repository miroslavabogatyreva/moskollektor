#!/usr/bin/env python3
"""Симулированные отказы и их предвестники по синтетическому паспорту. SL.10 (MOS-263).

Что делает. По паспорту канала (вид оборудования, дата ввода, срок службы) и датам
поверок или ТО моделирует начало ухудшения по интенсивности Вейбулла. В этот
момент появляется «предупреждение прибора», а через P-F — искусственный отказ.
Есть и ложные предупреждения без запланированного отказа. Интенсивность зависит
только от проверок, известных до суток предупреждения. Более поздняя поверка
не меняет уже наблюдённое предупреждение и не отменяет назначенный отказ — это
допущение демонстрации, не модель эффективности ремонта. Таблица параметров —
docs/proof/2026-09-28-sensor-model/simulation.md, словарь PARAMS обязан с ней совпадать.

Чего НЕ читает. Реальные отказы (failures.csv, smvu.model_failure_*). Вход — только
паспорт и даты проверок: выведи мы симулированные отказы из реальных, модель
«с синтетикой» выучила бы подсмотренный ответ.

Детерминизм. Случайное число суток — blake2b от строки «соль:канал:сутки», своё
на каждую пару «канал × сутки». Поэтому тик считает только нужные ему несколько
суток, а не всю историю, и тот же канал отказывает в те же сутки на любом прогоне
и любой машине.

Живёт в backend, а не в code/: предвестник нужен тику app.worker.sensor_scores.
    python -m app.domain.failure_sim --selfcheck               (из backend/)
    python -m app.domain.failure_sim --data ../docs/proof/2026-09-28-sensor-model/data \\
        --out ../docs/proof/2026-09-28-sensor-model/sim_failures.csv
"""

import argparse
import csv
import hashlib
import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
SALT = "sim-v3-causal"
DAY = 365.25  # суток в году для η

M = math.sqrt(10)  # ГОСТ Р 27.303-2021, табл. В.4: один ранг O = ×√10
PF_DAYS = 2.0  # интервал P-F — ДОПУЩЕНИЕ, чувствительность 1, 2 и 4 сут
FALSE_RATIO = 1.0  # ложных предвестников на один истинный — ДОПУЩЕНИЕ
# object_kind → класс, форма β, интервал поверки/ТО в сутках (None — точки нет),
# множитель при просрочке. η считается из срока службы паспорта: L / Γ(1 + 1/β).
PARAMS = {
    "DESM": ("ОПС, электроника", 1.0, None, None),
    "DEHT": ("ОПС, электроника", 1.0, None, None),
    "DEMC": ("ОПС, электроника", 1.0, None, None),
    "DEMO": ("ОПС, электроника", 1.0, None, None),
    "DEGL": ("ОПС, электроника", 1.0, None, None),
    "DEDR": ("ОПС, контакт", 1.0, None, None),
    "DEHA": ("ОПС, контакт", 1.0, None, None),
    "DEH9": ("ОПС, контакт", 1.0, None, None),
    "SWSC": ("ОПС, электроника", 1.0, None, None),
    "DEGD": ("АКМ, средство измерений", 1.0, 365, M),
    "DETM": ("средство измерений", 1.0, 365, M),
    "DEFL": ("СДУК, электроника", 1.0, None, None),
    "PUCE": ("СДУК, механика", 2.2, 182, M),
    "ATFA": ("СДУК, механика", 2.2, 182, M),
    "ELUP": ("электропитание", 1.0, None, None),
    "SWPH": ("электропитание", 1.0, None, None),
    "SWAV": ("электропитание", 1.0, None, None),
    "SWMS": ("электропитание", 1.0, None, None),
    "SWUR": ("электропитание", 1.0, None, None),
}
# Вид датчика выгрузки → object_kind паспорта (db/seed/sensor_demo.sql, syn_kind).
KIND_OF_SENSOR = {
    "Газовый датчик": "DEGD", "Датчик температуры": "DETM", "Состояние насоса": "PUCE",
    "Состояние вентилятора": "ATFA", "Датчик затопления": "DEFL", "Датчик дыма": "DESM",
    "Тепловой датчик": "DEHT", "Ручной извещатель": "DEMC", "ИБП": "ELUP",
    "Состояние фазы": "SWPH", "КД АВ": "SWAV", "Переключатель": "SWMS",
    "Состояние УИР-Р": "SWUR", "Состояние охраны": "SWSC", "Датчик движения": "DEMO",
    "Стекло": "DEGL", "КД Дверь": "DEDR", "КД Люк": "DEHA", "9-секционный люк": "DEH9",
}


def eta_days(life_years, beta):
    """Масштаб Вейбулла в сутках: срок службы паспорта считаем MTTF."""
    return life_years * DAY / math.gamma(1 + 1 / beta)


def uniform(salt, cid, d):
    """Равномерное [0, 1) пары «канал × сутки»: blake2b, 8 байт."""
    h = hashlib.blake2b(f"{salt}:{cid}:{d.toordinal()}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big") / 2**64


def overdue(d, age, checks, interval):
    """Просрочена ли поверка/ТО на сутки d: последняя проверка раньше d дальше
    интервала; проверок не было — возраст больше интервала."""
    if interval is None:
        return False
    last = None
    for c in checks:  # checks отсортированы
        if c >= d:
            break
        last = c
    return (d - last).days > interval if last else age > interval


def day_prob(d, in_service, beta, eta, checks=(), interval=None, mult=1.0):
    """Вероятность начала ухудшения в сутки d: 1 − exp(−M·ΔH), ΔH — прирост
    интенсивности Вейбулла. Истинный предвестник назначит отказ через P-F;
    до ввода оборудования вероятность равна нулю."""
    a = (d - in_service).days
    if a < 0:
        return 0.0
    dh = ((a + 1) / eta) ** beta - (a / eta) ** beta
    if overdue(d, a, checks, interval):
        dh *= mult
    return 1 - math.exp(-dh)


def _at(d, frac):
    return datetime.combine(d, datetime.min.time(), MSK) + timedelta(hours=24 * frac)


def channel(cid, in_service, beta, eta, start, end, checks=(), interval=None,
            mult=1.0, salt=SALT, pf_days=PF_DAYS, false_ratio=FALSE_RATIO):
    """Причинный поток предупреждений и назначенных ими отказов в [start, end].

    Сначала наблюдение, затем исход через pf_days: future checks не могут
    переписать наблюдение. Смена P-F сдвигает отказы, а не предвестники.
    Полный прогон и короткое окно дают одни и те же события внутри окна.
    """
    if not math.isfinite(pf_days) or pf_days < 0:
        raise ValueError("pf_days должен быть конечным неотрицательным числом")
    if not math.isfinite(false_ratio) or false_ratio < 0:
        raise ValueError("false_ratio должен быть конечным неотрицательным числом")
    checks = sorted(checks)
    pf = timedelta(days=pf_days)
    fails, pre = [], []
    # Предвестники до start тоже могут назначить отказ внутри запрошенного окна.
    d = start - timedelta(days=math.ceil(pf_days))
    while d <= end:
        p = day_prob(d, in_service, beta, eta, checks, interval, mult)
        if p > 0:
            u = uniform(salt, cid, d)
            if u < p:
                t = _at(d, u / p)
                if start <= (t + pf).date() <= end:
                    fails.append(t + pf)
                if start <= d:
                    pre.append((t, True))
            q = min(false_ratio * p, 1.0)
            v = uniform(salt + ":false", cid, d)
            if start <= d and v < q:
                pre.append((_at(d, v / q), False))
        d += timedelta(days=1)
    pre.sort()
    return fails, pre


def params_of(p):
    """Паспорт {"object_kind", "life", …} → (β, η в сутках, интервал, множитель)."""
    _, beta, interval, mult = PARAMS[p["object_kind"]]
    return beta, eta_days(p["life"], beta), interval, mult or 1.0


def simulate(passports, checks, start, end, salt=SALT, pf_days=PF_DAYS,
             false_ratio=FALSE_RATIO):
    """passports: {channel_id: {"object_kind", "in_service", "life"}};
    checks: {channel_id: [date, …]}. Отдаёт (отказы [(channel_id, datetime)],
    предвестники [(channel_id, datetime, истинный ли)]), всё по времени."""
    fails, pre = [], []
    for cid, p in sorted(passports.items()):
        beta, eta, interval, mult = params_of(p)
        f, pr = channel(cid, p["in_service"], beta, eta, start, end,
                        checks.get(cid, ()), interval, mult, salt, pf_days, false_ratio)
        fails += [(cid, t) for t in f]
        pre += [(cid, t, true) for t, true in pr]
    fails.sort(key=lambda x: (x[1], x[0]))
    pre.sort(key=lambda x: (x[1], x[0]))
    return fails, pre


def load(data):
    """passports.csv и checks.csv каталога data — и ничего больше."""
    data = Path(data)
    passports, checks = {}, {}
    with open(data / "passports.csv", newline="") as f:
        for r in csv.DictReader(f):
            passports[int(r["channel_id"])] = {
                "object_kind": r["object_kind"],
                "in_service": date.fromisoformat(r["in_service_from"]),
                "life": int(r["service_life_years"]),
            }
    with open(data / "checks.csv", newline="") as f:
        for r in csv.DictReader(f):
            checks.setdefault(int(r["channel_id"]), []).append(
                date.fromisoformat(r["measured_at"][:10]))
    return passports, checks


def _selfcheck():
    T, N = 365, 6_000
    start, end = date(2020, 1, 1), date(2020, 1, 1) + timedelta(days=T - 1)

    def share(beta, eta, **kw):
        hit = cnt = 0
        for cid in range(N):
            f, _ = channel(cid, start, beta, eta, start, end, false_ratio=0, **kw)
            hit += bool(f)
            cnt += len(f)
        return hit / N, cnt / N

    def near(got, want, what):
        se = math.sqrt(want * (1 - want) / N)
        assert abs(got - want) < 4 * se, f"{what}: {got:.4f} против {want:.4f}"

    # β = 1: доля с отказом — экспонента
    got, _ = share(1.0, 1000.0)
    near(got, 1 - math.exp(-T / 1000), "β=1")
    # β = 2,2: среднее число отказов — накопленная интенсивность (T/η)^β
    _, mean = share(2.2, 800.0)
    want = (T / 800) ** 2.2
    assert abs(mean - want) < 4 * math.sqrt(want / N), (mean, want)
    # постоянная просрочка: интенсивность ×M
    got, _ = share(1.0, 3000.0, checks=[], interval=0, mult=M)
    near(got, 1 - math.exp(-M * T / 3000), "просрочка")
    # до ввода отказов нет
    late = start + timedelta(days=200)
    for cid in range(2000):
        f, _ = channel(cid, late, 1.0, 50.0, start, end)
        assert all(t.date() >= late for t in f)
    # предвестник: у каждого отказа ровно за P-F, ложных — около FALSE_RATIO на истинный
    true = false = 0
    for cid in range(3000):
        f, pre = channel(cid, start - timedelta(days=400), 1.0, 200.0, start, end)
        ts = {t for t, ok in pre if ok}
        assert all(t - timedelta(days=PF_DAYS) in ts for t in f if (t - timedelta(days=PF_DAYS)).date() >= start)
        assert all(t + timedelta(days=PF_DAYS) in {x for x in f} or (t + timedelta(days=PF_DAYS)).date() > end for t in ts)
        true += len(ts)
        false += sum(1 for _, ok in pre if not ok)
    assert abs(false / true - FALSE_RATIO) < 0.1, (true, false)
    # детерминизм, соль и независимость от периода
    pp = {i: {"object_kind": k, "in_service": date(2015, 1, 1), "life": 10}
          for i, k in enumerate(PARAMS)}
    a = simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30))
    assert a == simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30))
    assert a[0] != simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30), salt="другая")[0]
    b = simulate(pp, {}, date(2024, 1, 1), date(2024, 12, 31))
    assert b[0] == [x for x in a[0] if x[1].year == 2024]
    # P-F сдвигает назначенные отказы, а наблюдения остаются прежними.
    c = simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30), pf_days=4)
    assert c[0] != a[0] and c[1] == a[1]
    # просрочка считается по последней проверке раньше суток
    assert not overdue(date(2024, 6, 1), 999, [date(2024, 1, 1)], 365)
    assert overdue(date(2025, 6, 1), 999, [date(2024, 1, 1)], 365)
    assert overdue(date(2024, 6, 1), 400, [], 365) and not overdue(date(2024, 6, 1), 300, [], 365)
    assert len(PARAMS) == 19 and set(KIND_OF_SENSOR.values()) == set(PARAMS)
    print("failure_sim selfcheck ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--data")
    ap.add_argument("--out", help="CSV отказов; предвестники — рядом, *_precursors.csv")
    ap.add_argument("--start", default="2022-04-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--pf", type=float, default=PF_DAYS)
    ap.add_argument("--false-ratio", type=float, default=FALSE_RATIO)
    a = ap.parse_args()
    if a.selfcheck or not a.data:
        _selfcheck()
        return
    passports, checks = load(a.data)
    fails, pre = simulate(passports, checks, date.fromisoformat(a.start),
                          date.fromisoformat(a.end), pf_days=a.pf, false_ratio=a.false_ratio)
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["channel_id", "started_at"])
            for cid, t in fails:
                w.writerow([cid, t.isoformat(timespec="seconds")])
        with open(a.out.replace(".csv", "_precursors.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["channel_id", "observed_at", "is_true"])
            for cid, t, ok in pre:
                w.writerow([cid, t.isoformat(timespec="seconds"), "t" if ok else "f"])
    по_годам = Counter(t.year for _, t in fails)
    по_видам = Counter(passports[c]["object_kind"] for c, _ in fails)
    print(f"симулированных отказов: {len(fails)} на {len({c for c, _ in fails})} каналах")
    print(f"предвестников: {len(pre)}, истинных {sum(ok for *_, ok in pre)}")
    print("по годам:", dict(sorted(по_годам.items())))
    print("по видам:", dict(по_видам.most_common()))


if __name__ == "__main__":
    main()
