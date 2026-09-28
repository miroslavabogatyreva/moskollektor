#!/usr/bin/env python3
"""Симулированные отказы по синтетическому паспорту. Задача SL.10 (MOS-263).

Что делает. По паспорту канала (вид оборудования, дата ввода, срок службы) и датам
поверок или ТО считает, в какие сутки канал отказал бы, если бы оборудование
ломалось по Вейбуллу с параметрами из регламента и книг по надёжности. Таблица
параметров с источником каждого числа — docs/proof/2026-09-28-sensor-model/simulation.md,
словарь PARAMS ниже обязан с ней совпадать.

Чего НЕ читает. Реальные отказы (failures.csv, smvu.model_failure_*). Вход — только
passports.csv и checks.csv: выведи мы симулированные отказы из реальных, модель
«с синтетикой» выучила бы подсмотренный ответ.

Детерминизм. Случайное число на сутки даёт random.Random("sim-v1:<channel_id>"),
по одному числу на каждые сутки начиная с EPOCH. Тот же канал с тем же паспортом
отказывает в те же сутки на любом прогоне и на любой машине.

Только стандартная библиотека.
    python3 code/failure_sim.py --selfcheck
    python3 code/failure_sim.py --data docs/proof/2026-09-28-sensor-model/data \\
        --out docs/proof/2026-09-28-sensor-model/sim_failures.csv
"""

import argparse
import csv
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
EPOCH = date(2019, 1, 1)  # начало архива СМВУ: отсюда идут случайные числа
SALT = "sim-v1"
DAY = 365.25  # суток в году для η

M = math.sqrt(10)  # ГОСТ Р 27.303-2021, табл. В.4: один ранг O = ×√10
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


def eta_days(life_years, beta):
    """Масштаб Вейбулла в сутках: срок службы паспорта считаем MTTF."""
    return life_years * DAY / math.gamma(1 + 1 / beta)


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


def channel(
    cid,
    in_service,
    beta,
    eta,
    start,
    end,
    checks=(),
    interval=None,
    mult=1.0,
    salt=SALT,
):
    """Отказы одного канала за [start, end] (даты, включительно) — список datetime МСК."""
    rng = random.Random(f"{salt}:{cid}")
    out, d = [], EPOCH
    checks = sorted(checks)
    while d <= end:
        u = rng.random()  # число тратится на каждые сутки, даже вне периода
        if d >= start:
            a = (d - in_service).days
            if a >= 0:
                dh = ((a + 1) / eta) ** beta - (a / eta) ** beta
                if overdue(d, a, checks, interval):
                    dh *= mult
                p = 1 - math.exp(-dh)
                if u < p:
                    out.append(
                        datetime.combine(d, datetime.min.time(), MSK)
                        + timedelta(hours=24 * u / p)
                    )
        d += timedelta(days=1)
    return out


def simulate(passports, checks, start, end, salt=SALT):
    """passports: {channel_id: {"object_kind", "in_service", "life"}};
    checks: {channel_id: [date, …]}. Отдаёт [(channel_id, datetime МСК)] по времени."""
    out = []
    for cid, p in sorted(passports.items()):
        _, beta, interval, mult = PARAMS[p["object_kind"]]
        for t in channel(
            cid,
            p["in_service"],
            beta,
            eta_days(p["life"], beta),
            start,
            end,
            checks.get(cid, ()),
            interval,
            mult or 1.0,
            salt,
        ):
            out.append((cid, t))
    out.sort(key=lambda x: (x[1], x[0]))
    return out


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
                date.fromisoformat(r["measured_at"][:10])
            )
    return passports, checks


def _selfcheck():
    T, N = 365, 20_000
    start, end = date(2020, 1, 1), date(2020, 1, 1) + timedelta(days=T - 1)

    def share(beta, eta, **kw):
        hit = cnt = 0
        for cid in range(N):
            f = channel(cid, start, beta, eta, start, end, **kw)
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
        assert all(t.date() >= late for t in channel(cid, late, 1.0, 50.0, start, end))
    # детерминизм и соль
    pp = {
        i: {"object_kind": k, "in_service": date(2015, 1, 1), "life": 10}
        for i, k in enumerate(PARAMS)
    }
    a = simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30))
    assert a == simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30))
    assert a != simulate(pp, {}, date(2022, 4, 1), date(2026, 6, 30), salt="другая")
    # период не сдвигает случайные числа: отказы 2024 года одни и те же
    b = simulate(pp, {}, date(2024, 1, 1), date(2024, 12, 31))
    assert b == [x for x in a if x[1].year == 2024]
    # просрочка считается по последней проверке раньше суток
    assert not overdue(date(2024, 6, 1), 999, [date(2024, 1, 1)], 365)
    assert overdue(date(2025, 6, 1), 999, [date(2024, 1, 1)], 365)
    assert overdue(date(2024, 6, 1), 400, [], 365) and not overdue(
        date(2024, 6, 1), 300, [], 365
    )
    assert len(PARAMS) == 19
    print("failure_sim selfcheck ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--data")
    ap.add_argument("--out")
    ap.add_argument("--start", default="2022-04-01")
    ap.add_argument("--end", default="2026-06-30")
    a = ap.parse_args()
    if a.selfcheck or not a.data:
        _selfcheck()
        return
    passports, checks = load(a.data)
    sim = simulate(
        passports, checks, date.fromisoformat(a.start), date.fromisoformat(a.end)
    )
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["channel_id", "started_at"])
            for cid, t in sim:
                w.writerow([cid, t.isoformat(timespec="seconds")])
    по_годам = Counter(t.year for _, t in sim)
    по_видам = Counter(passports[c]["object_kind"] for c, _ in sim)
    print(f"симулированных отказов: {len(sim)} на {len({c for c, _ in sim})} каналах")
    print("по годам:", dict(sorted(по_годам.items())))
    print("по видам:", dict(по_видам.most_common()))


if __name__ == "__main__":
    main()
