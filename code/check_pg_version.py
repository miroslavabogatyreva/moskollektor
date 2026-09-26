#!/usr/bin/env python3
"""Минимум PostgreSQL — 15, а не 12. Строка приёмки НФ-80.

ТЗ разд. 11 пишет «PostgreSQL 12 и выше». На 12-ю ветку наша схема не
накатится: UNIQUE NULLS NOT DISTINCT появился в 15-й, и эта конструкция
стоит в db/migrations/003_permits.sql. Ловушка закрывается словами в
docs/install.md, а не правкой миграции.

Три проверки, по строке каждая:
  А — в docs/install.md есть подстрока «Минимальная версия PostgreSQL — 15».
      Нет — СБОЙ.
  Б — хотя бы в одном db/migrations/*.sql есть «UNIQUE NULLS NOT DISTINCT»
      в коде, не в комментарии. Перед поиском у строки отрезается всё
      начиная с «--». Нет в коде — ВНИМАНИЕ и код 0.
  В — DATABASE_URL задан: SHOW server_version_num через asyncpg, число
      ≥ 150000. Меньше — СБОЙ. Нет переменной — ВНИМАНИЕ: ПРОПУСК, код 0.
      Слово ВНИМАНИЕ нужно, потому что delivery/check-all.sh при коде 0
      показывает только строки с ним.

Запуск:
    python3 code/check_pg_version.py
    python3 code/check_pg_version.py --selftest
Код возврата 1 при СБОЕ.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
ФРАЗА = "Минимальная версия PostgreSQL — 15"
КОНСТРУКЦИЯ = "UNIQUE NULLS NOT DISTINCT"
МИНИМУМ = 150000  # server_version_num: 15.0 = 150000
НЕТ_URL = "ВНИМАНИЕ: ПРОПУСК проверки В — нет DATABASE_URL"


def проверка_а(текст: str) -> str | None:
    """None, если подстрока на месте. Иначе строка СБОЙ."""
    if ФРАЗА not in текст:
        return "СБОЙ docs/install.md не содержит «Минимальная версия PostgreSQL — 15»"
    return None


def код_без_комментариев(текст: str) -> str:
    """Всё начиная с «--» на строке — комментарий, в поиск не идёт."""
    return "\n".join(строка.split("--", 1)[0] for строка in текст.splitlines())


def проверка_б(каталог: Path) -> str | None:
    """None, если конструкция нашлась в коде. Иначе строка ВНИМАНИЕ, не СБОЙ."""
    for файл in sorted(каталог.glob("*.sql")):
        if КОНСТРУКЦИЯ in код_без_комментариев(файл.read_text(encoding="utf-8")):
            return None
    return "ВНИМАНИЕ: причины нет, минимум можно снизить"


def проверка_в(версия: str | None) -> str:
    """версия — вывод SHOW server_version_num, или None если базу не звали."""
    if версия is None:
        return НЕТ_URL
    число = версия.strip()
    if not число.isdigit() or int(число) < МИНИМУМ:
        return f"СБОЙ server_version_num={число or 'пусто'}, нужно ≥ {МИНИМУМ}"
    return f"OK server_version_num={число} ≥ {МИНИМУМ}"


def версия_стенда(dsn: str) -> str:
    """SHOW server_version_num через asyncpg, как code/check_auth.py."""
    import asyncpg

    async def m():
        c = await asyncpg.connect(dsn)
        try:
            return await c.fetchval("SHOW server_version_num")
        finally:
            await c.close()

    return str(asyncio.run(m()))


def самопроверка() -> int:
    """«— 15 и выше» принято, «12» отвергнута, комментарий без UNIQUE — ВНИМАНИЕ."""
    with tempfile.TemporaryDirectory() as каталог:
        образец = Path(каталог) / "install.md"
        образец.write_text(
            "шапка\nМинимальная версия PostgreSQL — 15 и выше\nхвост\n",
            encoding="utf-8",
        )
        if проверка_а(образец.read_text(encoding="utf-8")) is not None:
            print("СБОЙ самопроверка: «— 15 и выше» не принята")
            return 1
        образец.write_text(
            "шапка\n**Минимальная версия PostgreSQL — 12, а не 12.**\nхвост\n",
            encoding="utf-8",
        )
        if проверка_а(образец.read_text(encoding="utf-8")) is None:
            print("СБОЙ самопроверка: фраза с «12» принята")
            return 1
        миграции = Path(каталог) / "migrations"
        миграции.mkdir()
        (миграции / "003.sql").write_text(
            "-- NULLS NOT DISTINCT, чтобы и её нельзя было завести дважды\n",
            encoding="utf-8",
        )
        беда = проверка_б(миграции)
        if беда is None or "причины нет" not in беда:
            print("СБОЙ самопроверка: комментарий без UNIQUE принят за причину")
            return 1
        (миграции / "003.sql").write_text(
            "-- Целевая СУБД: PostgreSQL 15+ (UNIQUE NULLS NOT DISTINCT), поставка — 18.6.\n",
            encoding="utf-8",
        )
        беда = проверка_б(миграции)
        if беда is None or "причины нет" not in беда:
            print("СБОЙ самопроверка: фраза только в комментарии принята за причину")
            return 1
        (миграции / "003.sql").write_text(
            "UNIQUE NULLS NOT DISTINCT (from_status, to_status)\n",
            encoding="utf-8",
        )
        if проверка_б(миграции) is not None:
            print("СБОЙ самопроверка: конструкция в коде не принята")
            return 1
    if проверка_в(None) != НЕТ_URL:
        print("СБОЙ самопроверка: нет версии — не ВНИМАНИЕ")
        return 1
    if not проверка_в("140006").startswith("СБОЙ"):
        print("СБОЙ самопроверка: 14.6 принята")
        return 1
    if not проверка_в("180006").startswith("OK"):
        print("СБОЙ самопроверка: 18.6 не принята")
        return 1
    print(
        "OK самопроверка: «— 15 и выше» принята, «12» отвергнута, "
        "фраза только в комментарии — ВНИМАНИЕ, 14.6 СБОЙ, 18.6 OK, нет URL — ВНИМАНИЕ"
    )
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return самопроверка()
    сбой = False
    беда = проверка_а((КОРЕНЬ / "docs" / "install.md").read_text(encoding="utf-8"))
    print("OK docs/install.md называет минимум 15" if беда is None else беда)
    сбой = сбой or беда is not None
    беда = проверка_б(КОРЕНЬ / "db" / "migrations")
    print("OK в db/migrations есть UNIQUE NULLS NOT DISTINCT" if беда is None else беда)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        строка = проверка_в(None)
    else:
        try:
            строка = проверка_в(версия_стенда(dsn))
        except Exception as e:
            строка = f"СБОЙ база не ответила: {str(e).strip().splitlines()[-1]}"
    print(строка)
    сбой = сбой or строка.startswith("СБОЙ")
    return 1 if сбой else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
