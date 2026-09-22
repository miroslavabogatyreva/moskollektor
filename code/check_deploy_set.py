#!/usr/bin/env python3
"""Команда выкладки везёт всё, что копирует `backend/Dockerfile`. Задача 1.15, MOS-160.

Зачем. Пропуск каталога в команде выкладки не роняет сборку: `COPY` берёт то, что
лежит на сервере, а лежит там прошлая выкладка, — и образ молча уезжает вчерашним.
22.09.2026 так и вышло: выложили `backend db code` без `contracts`, `docker build`
прошёл, а `python code/check_metrics.py --selfcheck` внутри образа упал
`FileNotFoundError: /app/contracts/failure.v3.json`.

Что сверяем. Первый элемент пути каждого `COPY` в `backend/Dockerfile` — против
списка каталогов в команде `git archive origin/master …` из `docs/server.md`.
Каталог из `Dockerfile`, которого нет в команде, — ошибка; лишний каталог в команде
ошибкой не считаем, возить больше нужного не вредно.
"""

import re
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
DOCKERFILE = КОРЕНЬ / "backend" / "Dockerfile"
ИНСТРУКЦИЯ = КОРЕНЬ / "docs" / "server.md"


def копируемые(текст: str) -> set[str]:
    """Каталоги верхнего уровня из строк COPY: «COPY db/migrations ./db» -> «db»."""
    итог = set()
    for строка in текст.splitlines():
        слова = строка.strip().split()
        if len(слова) >= 3 and слова[0] == "COPY":
            итог.add(слова[1].split("/", 1)[0])
    return итог


def выкладываемые(текст: str) -> set[str]:
    """Каталоги из команды `git archive origin/master A B C | …` в инструкции."""
    найдено = re.search(r"git archive origin/master ([^|\\\n]+)", текст)
    assert найдено, f"{ИНСТРУКЦИЯ}: команды `git archive origin/master …` нет вовсе"
    return {слово.split("/", 1)[0] for слово in найдено.group(1).split()}


def проверить() -> list[str]:
    из_образа = копируемые(DOCKERFILE.read_text(encoding="utf-8"))
    из_команды = выкладываемые(ИНСТРУКЦИЯ.read_text(encoding="utf-8"))
    нехватка = sorted(из_образа - из_команды)
    return [
        f"{DOCKERFILE.name} копирует «{к}/», а команда выкладки в "
        f"{ИНСТРУКЦИЯ.name} его не везёт — в образ уедет прошлая выкладка"
        for к in нехватка
    ]


def _selfcheck():
    """Разбор обоих файлов на образцах: проверка обязана ловить пропуск каталога."""
    образец = "FROM python\nCOPY backend/app ./app\nCOPY contracts ./contracts\nCOPY x.txt .\n"
    assert копируемые(образец) == {"backend", "contracts", "x.txt"}, копируемые(образец)
    # «COPY x.txt .» даёт имя «x.txt» — и пусть: в команде выкладки такого слова нет,
    # проверка пожалуется, и это верно. Файл из корня в образ тоже надо возить.
    assert копируемые("RUN pip install\nCOPY a\n") == set(), (
        "не COPY источник назначение"
    )
    команда = (
        "текст\ngit archive origin/master backend db code contracts | ssh root@x \\\n"
    )
    assert выкладываемые(команда) == {"backend", "db", "code", "contracts"}
    # Красный случай: в образ едет contracts, в команде его нет — одна жалоба.
    беда = копируемые(образец) - выкладываемые(
        "git archive origin/master backend x.txt\n"
    )
    assert беда == {"contracts"}, беда
    # И живые файлы должны сходиться — иначе проверка зелёная на выдуманных данных.
    assert not проверить(), "\n".join(проверить())
    print("самопроверка ok: разбор COPY, разбор команды, пропуск каталога виден")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
        sys.exit(0)
    беды = проверить()
    for б in беды:
        print(f"РАСХОЖДЕНИЕ: {б}")
    if not беды:
        print("выкладка и Dockerfile сходятся: все каталоги COPY едут на стенд")
    sys.exit(1 if беды else 0)
