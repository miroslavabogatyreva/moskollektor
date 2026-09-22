#!/usr/bin/env python3
"""Хеши миграций в git совпадают с накатанными на стенде — ДО сборки образа.

Зачем. 22.09.2026 коммит 5833175 поправил комментарий в уже накатанных 034 и 035.
Следующая пересборка migrate упала на сверке sha256 (backend/app/migrate.py),
api и worker три минуты не поднимались, /api/risks отвечал 502. Текст накатанной
миграции заморожен, а узнали об этом только на накате. Здесь то же сравнение, но
до сборки: файлы берутся из git по ссылке (по умолчанию origin/master — то, что
поедет на стенд), хеши — из public.schema_migration той базы, куда поедет.

Хеш считается так же, как в migrate.py: sha256(path.read_text().encode()).
read_text приводит переводы строк к \\n, поэтому и здесь \\r\\n и \\r -> \\n:
иначе файл с CRLF дал бы здесь ложный СБОЙ, а накат прошёл бы.

Печатает по строке на каждый расходящийся файл и итог. СБОЙ:
  - файл накатан, а его хеш в git другой (накат упадёт);
  - файл накатан, а в git его нет (переименовали или удалили накатанное).
Файлы в git, которых нет в базе, — не сбой: их накатит следующая выкладка.
Ссылка сравнения печатается хешем коммита: устаревший origin (не сделан
git fetch) сравнивал бы не то, что поедет.

Запуск:
    DATABASE_URL=postgresql://... python3 code/check_migration_hashes.py [--ref origin/master]
    python3 code/check_migration_hashes.py --selfcheck
Код возврата 1 при СБОЕ.
"""

import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
КАТАЛОГ = "db/migrations"


def хеш(содержимое: bytes) -> str:
    """sha256 как в migrate.py: текст с переводами строк \\n, в UTF-8."""
    текст = содержимое.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(текст.encode()).hexdigest()


def из_git(ref: str) -> tuple[str, dict[str, str]]:
    """(коммит, {имя файла: sha256}) для db/migrations/*.sql по ссылке git."""
    git = lambda *a: (
        subprocess.run(  # noqa: E731
            ["git", *a], cwd=КОРЕНЬ, capture_output=True, check=True
        ).stdout
    )
    коммит = git("rev-parse", "--short", ref).decode().strip()
    имена = [
        и
        for и in git("ls-tree", "--name-only", f"{ref}:{КАТАЛОГ}").decode().split()
        if и.endswith(".sql")
    ]
    return коммит, {и: хеш(git("show", f"{ref}:{КАТАЛОГ}/{и}")) for и in имена}


def расхождения(в_git: dict[str, str], в_базе: dict[str, str]) -> list[str]:
    """Что уронит накат: накатанный файл с другим хешем или без файла в git."""
    беды = []
    for имя, sha in sorted(в_базе.items()):
        if имя not in в_git:
            беды.append(f"{имя}: накатан, а в git его нет")
        elif в_git[имя] != sha:
            беды.append(f"{имя}: sha256 в git {в_git[имя][:12]}, накатан {sha[:12]}")
    return беды


async def из_базы(dsn: str) -> dict[str, str]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        return dict(
            await conn.fetch("SELECT filename, sha256 FROM public.schema_migration")
        )
    finally:
        await conn.close()


def самопроверка():
    # CRLF и LF дают один хеш — как у read_text в migrate.py.
    assert хеш(b"SELECT 1;\r\n") == хеш(b"SELECT 1;\n") == хеш(b"SELECT 1;\r")
    git = {"001_a.sql": "aa", "002_b.sql": "bb", "003_c.sql": "cc"}
    assert (
        расхождения(git, {"001_a.sql": "aa", "002_b.sql": "bb"}) == []
    )  # 003 ещё не накатан
    assert расхождения(git, {"001_a.sql": "aa", "002_b.sql": "XX"}) == [
        "002_b.sql: sha256 в git bb, накатан XX"
    ]
    assert расхождения({"001_a.sql": "aa"}, {"001_a.sql": "aa", "002_b.sql": "bb"}) == [
        "002_b.sql: накатан, а в git его нет"
    ]
    print(
        "самопроверка: CRLF, сдвинутый хеш, пропавший файл, ненакатанный файл — 4 случая"
    )


def main(argv):
    if "--selfcheck" in argv:
        самопроверка()
        return 0
    самопроверка()
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else "origin/master"
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("СБОЙ не задана DATABASE_URL — сверять хеши не с чем")
        return 1
    try:
        коммит, в_git = из_git(ref)
    except subprocess.CalledProcessError as e:
        print(f"СБОЙ git не отдал {ref}:{КАТАЛОГ}: {e.stderr.decode().strip()}")
        return 1
    try:
        в_базе = asyncio.run(из_базы(dsn))
    except Exception as e:  # noqa: BLE001 — любая беда связи это СБОЙ, а не трасса
        print(
            f"СБОЙ нет связи с базой: {(str(e).splitlines() or [type(e).__name__])[0]}"
        )
        return 1
    беды = расхождения(в_git, в_базе)
    for б in беды:
        print(f"СБОЙ {б}")
    новых = sorted(set(в_git) - set(в_базе))
    print(
        f"{'СБОЙ' if беды else 'OK'} {ref} ({коммит}): файлов в git {len(в_git)}, "
        f"накатано {len(в_базе)}, хеш расходится у {len(беды)}; "
        f"ещё не накатаны: {', '.join(новых) or 'нет'}"
    )
    return 1 if беды else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
