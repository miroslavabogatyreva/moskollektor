#!/usr/bin/env python3
"""Привязка графиков ТО/ППР АКМ к нашим объектам по количеству газоанализаторов.

Сравнивает число газовых каналов (тип «Газоанализаторы») на наших коллекторах и
листовых объектах с числом датчиков в объектах графика ППР и строки
«Газоанализаторы» графика ТО. Перестановочный тест: как часто случайные числа
дали бы не меньше совпадений.

Запуск: .venv/bin/python analysis/to_ppr/mapping.py
Разбор: analysis/to_ppr/mapping.md
"""

import collections
import csv
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANNELS = ROOT / "dataset/справочник_каналов_датчиков.csv"
OBJECTS = ROOT / "dataset/справочник_объектов_диспетчер.csv"
PPR = ROOT / "analysis/to_ppr/ppr_schedule.csv"
TO = ROOT / "analysis/to_ppr/to_schedule.csv"
N_SIM = 2000
SEED = 7


def load_tree():
    tree = {}
    with OBJECTS.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                tree[int(row["ид_объект"])] = (
                    int(row["иерархия_уровень"]),
                    int(row["родитель"]),
                    row["диспетчерское_название_объекта"],
                )
            except (TypeError, ValueError):
                continue
    return tree


def gas_counts(tree):
    """Число газоанализаторов по листовым объектам и по коллекторам."""
    by_leaf = collections.Counter()
    by_col = collections.Counter()
    with CHANNELS.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if "азо" not in (row.get("тип_датчика") or ""):
                continue
            try:
                leaf = int(row["ид_объект"])
            except (TypeError, ValueError):
                continue
            by_leaf[leaf] += 1
            level, parent, _name = tree.get(leaf, (None, None, "?"))
            by_col[parent if level == 3 else leaf] += 1
    return by_leaf, by_col


def ppr_counts():
    """Объекты графика ППР: имя, число датчиков, месяц."""
    out = []
    with PPR.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["этап"] == "начало_демонтажа_датчиков":
                out.append((row["объект"], int(row["кол_датчиков_шт"]), row["месяц_ппр"]))
    return out


def to_gas_counts():
    """Объекты графика ТО по строке «Газоанализаторы»."""
    out = {}
    with TO.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "азоанализатор" in row["вид_оборудования"]:
                out[row["объект_номер"]] = int(row["количество"])
    return out


def match_count(ours, theirs, tol):
    """Жадное сопоставление: сколько наших чисел нашли пару в пределах tol."""
    pool = list(theirs)
    matched = 0
    for x in ours:
        for i, y in enumerate(pool):
            if abs(x - y) <= tol:
                matched += 1
                pool.pop(i)
                break
    return matched


def permutation_p(ours, theirs, tol, n=N_SIM, seed=SEED):
    """Вероятность получить не меньше совпадений на случайных числах."""
    matched = match_count(ours, theirs, tol)
    random.seed(seed)
    lo, hi = min(theirs), max(theirs)
    sims = [
        match_count(ours, [random.randint(lo, hi) for _ in theirs], tol)
        for _ in range(n)
    ]
    ge = sum(1 for s in sims if s >= matched)
    return matched, statistics.mean(sims), (ge + 1) / (n + 1)


def main():
    tree = load_tree()
    by_leaf, by_col = gas_counts(tree)
    ppr = ppr_counts()
    ppr_numbers = [n for _name, n, _month in ppr]
    to_numbers = to_gas_counts()

    print("ППР, объектов:", len(ppr), "датчиков всего:", sum(ppr_numbers))
    print("ТО, объектов со строкой «Газоанализаторы»:", len(to_numbers))
    print("наших газоанализаторов всего:", sum(by_col.values()))
    print()
    for label, ours in (("коллекторы", list(by_col.values())), ("листовые", list(by_leaf.values()))):
        for tol in (0, 1):
            matched, mean, p = permutation_p(ours, ppr_numbers, tol)
            print(
                f"{label:<11} tol={tol}: совпало {matched}/{len(ours)}, "
                f"случайно в среднем {mean:.2f}, p = {p:.4f}"
            )
    print()
    print("кандидаты привязки (коллектор | наших | объект ППР | датчиков | месяц):")
    for col, n in by_col.most_common():
        name = tree.get(col, (None, None, "?"))[2]
        cands = [(o, m, month) for o, m, month in ppr if abs(m - n) <= 1]
        tail = ", ".join(f"{o} ({m}, {month})" for o, m, month in cands) or "— нет кандидата"
        print(f"  {name:<26} {n:>4} -> {tail}")
    print()
    print("датчиков у объектов ТО:", dict(sorted(to_numbers.items(), key=lambda kv: int(kv[0]))))


if __name__ == "__main__":
    main()
