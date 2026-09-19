"""Сверяет план сам с собой: числа в таблице блоков против строк задач.

Счётчик задач в шапке `docs/plan.md` отстаёт молча — 19.09.2026 у четырёх блоков
из десяти он врал (Q1 показывал 10 задач при 13, Q2 — 17 при 20). Никто этого
не замечал, потому что заметить можно только пересчитав руками.

Заодно ловит две соседние ошибки того же рода: номер задачи, занятый дважды,
и ссылку на строку приёмки, которой нет в docs/acceptance-test.md — номер ищется
по всей строке задачи, а не только после слова «приёмка»: в восьми строках плана
такие ссылки стоят прямо в тексте.

Запуск: python3 code/check_plan.py
Самопроверка: python3 code/check_plan.py --selftest
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "plan.md"
ACCEPT = ROOT / "docs" / "acceptance-test.md"

BLOCK_ROW = re.compile(r"\| `Q(\d)`[^|]*\|[^|]*\|[^|]*\| (\d+) \|")
TASK_ROW = re.compile(r"^\| (\d+)\.(\d+) \|")
ROW_ID = re.compile(r"\b(М|Ф|НФ)-(\d+)\b")


def check(plan_text, accept_text):
    """Возвращает список претензий. Пустой список — план сам с собой согласен."""
    lines = plan_text.split("\n")
    problems = []

    tasks = {}
    for line in lines:
        m = TASK_ROW.match(line)
        if m:
            tasks.setdefault(m.group(1), []).append(m.group(2))

    for line in lines:
        m = BLOCK_ROW.search(line)
        if m:
            block, declared = m.group(1), int(m.group(2))
            actual = len(tasks.get(block, []))
            if declared != actual:
                problems.append(
                    f"блок Q{block}: в таблице заявлено {declared} задач, строк {actual}"
                )

    for block, numbers in tasks.items():
        seen = set()
        for n in numbers:
            if n in seen:
                problems.append(f"номер задачи Q{block}.{n} занят дважды")
            seen.add(n)

    known = set(ROW_ID.findall(accept_text))
    for line in lines:
        if not TASK_ROW.match(line):
            continue
        # Всю строку, а не только хвост после слова «приёмка»: 19.09.2026 смежная
        # сессия посчитала — из 120 строк задач 43 обходятся без этого слова,
        # и в восьми из них номера строк приёмки стоят прямо в тексте (0.4 → М-18,
        # М-19, М-20; 0.6 → НФ-69, Ф-84…Ф-87). Проверка их не видела.
        for prefix, num in ROW_ID.findall(line):
            if (prefix, num) not in known:
                task = TASK_ROW.match(line).group(0).strip("| ")
                problems.append(
                    f"задача {task} ссылается на строку {prefix}-{num}, "
                    "которой нет в docs/acceptance-test.md"
                )
    return problems


def selftest():
    accept = "| Ф-93 | фильтры | … |\n| НФ-89 | обновление | … |"

    ok = "| `Q5` Интерфейс | MOS-6 | `frontend/` | 2 | 0 | ⏳ |\n| 5.1 | а | б · приёмка: Ф-93 | — | Мирослава |\n| 5.2 | в | г · приёмка: НФ-89 | — | Мирослава |"
    assert check(ok, accept) == [], check(ok, accept)

    bad_count = ok.replace("| 2 | 0 |", "| 7 | 0 |")
    assert any("заявлено 7" in p for p in check(bad_count, accept))

    dup = ok + "\n| 5.1 | д | е · приёмка: Ф-93 | — | Мирослава |"
    assert any("занят дважды" in p for p in check(dup, accept))

    ghost = ok.replace("приёмка: НФ-89", "приёмка: НФ-777")
    assert any("НФ-777" in p for p in check(ghost, accept))

    # Номер в тексте задачи, без слова «приёмка» рядом, проверяется наравне с остальными
    mention = ok.replace("| 5.2 | в |", "| 5.2 | правит Ф-404 |")
    assert any("Ф-404" in p for p in check(mention, accept)), check(mention, accept)

    # …а живой номер в тексте задачи краснить не должен
    live = ok.replace("| 5.2 | в |", "| 5.2 | правит Ф-93 |")
    assert check(live, accept) == [], check(live, accept)
    print("самопроверка прошла: шесть случаев")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    found = check(PLAN.read_text(), ACCEPT.read_text())
    if found:
        print(f"план расходится сам с собой, {len(found)} мест:")
        for p in found:
            print(" ", p)
        sys.exit(1)
    print("план согласован: счётчики блоков, номера задач и ссылки на приёмку сходятся")
