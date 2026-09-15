"""Накат db/migrations/*.sql. HLD разд. 7.1 и 7.5.

Файлы берутся глобом и сортируются по имени (номер в начале, три цифры,
разрыв в нумерации не помеха). Каждый — своя транзакция. У уже накатанного
файла сменился sha256 — падаем: значит кто-то поправил применённую миграцию,
и молча это пропускать опаснее, чем остановить накат.

Пути внутри контейнера: WORKDIR /app, backend/app скопирован в ./app,
db/migrations — в ./db/migrations (см. backend/Dockerfile). Вне этого layout
(запуск не из образа) MIGRATIONS_DIR разъедется — скрипт для этого не рассчитан.

Бутстрап на чистой базе не нужен вовсе: public.schema_migration пуста, и
первый же прогон накатывает все файлы по порядку с нуля — обычный путь.
Он нужен только на стенде, где часть файлов уже накатана руками ДО того, как
появился этот скрипт (наш случай 15.09.2026 с 001…006 — см.
deploy/bootstrap-schema-migration.sql): без бутстрапа первый прогон попробует
выполнить CREATE TABLE по уже существующим объектам и упадёт. Стенд с
чистого листа мы ни разу не поднимали — это не проверено, только рассуждение
по коду.
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"

CREATE_JOURNAL = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    filename   text PRIMARY KEY,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def _pending(files, applied):
    """files: [(name, sql, sha256)] по возрастанию имени. applied: {name: sha256}."""
    result = []
    for name, sql, sha256 in files:
        if name in applied:
            if applied[name] != sha256:
                raise ValueError(f"{name}: sha256 разошёлся с накатанным")
            continue
        result.append((name, sql, sha256))
    return result


async def main():
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text()
        files.append((path.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute(CREATE_JOURNAL)
        applied = dict(await conn.fetch("SELECT filename, sha256 FROM public.schema_migration"))
        pending = _pending(files, applied)
        for name, sql, sha256 in pending:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO public.schema_migration (filename, sha256) VALUES ($1, $2)",
                    name, sha256,
                )
            print(f"{name}: накатан")
        print(f"готово: {len(pending)} новых, {len(files) - len(pending)} уже были")
    finally:
        await conn.close()


def _selfcheck():
    assert _pending([("001_a.sql", "s", "x")], {}) == [("001_a.sql", "s", "x")]
    assert _pending([("001_a.sql", "s", "x")], {"001_a.sql": "x"}) == []
    try:
        _pending([("001_a.sql", "s", "x")], {"001_a.sql": "y"})
    except ValueError:
        pass
    else:
        raise AssertionError("должен упасть на расхождении sha256")


if __name__ == "__main__":
    _selfcheck()
    try:
        asyncio.run(main())
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
