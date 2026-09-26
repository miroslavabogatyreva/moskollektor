"""Накат db/migrations/*.sql, а после них — сидов из db/seed/*.sql. HLD разд. 7.1 и 7.5.

Файлы берутся глобом и сортируются по имени (номер в начале, три цифры,
разрыв в нумерации не помеха). Каждый — своя транзакция. У уже накатанного
файла сменился sha256 — падаем: значит кто-то поправил применённую миграцию,
и молча это пропускать опаснее, чем остановить накат.

**Сиды идут после всех миграций и накатываются заново на каждом прогоне.**
В журнале public.schema_migration их нет, и это сознательно: сид держит
справочник и права ролей, то есть то, что в git меняется правкой файла, а не
миграцией. Накатывать его один раз — значит навсегда отдать его содержимое
первому прогону, а всё, что попало в git позже, в базу не доедет. Ровно так
18.09.2026 на стенде отстали права: в db/seed/rbac.sql появились settings.read
и settings.write, база их не получила, и admin1 отвечал 403 на GET /api/settings.
Идемпотентность держат сами сиды: вставки написаны INSERT … ON CONFLICT.

Порядок сидов — по имени файла. Зависимости между файлами нет: каждый пишет
в таблицы, которые заводят миграции, то есть к моменту сидов они уже созданы.

Каталог миграций ищется в двух местах: ./db/migrations рядом с пакетом (так
лежит в образе — WORKDIR /app, backend/app скопирован в ./app, db/migrations
в ./db/migrations, см. backend/Dockerfile) и на уровень выше (так лежит
в репозитории: backend/app/migrate.py и db/migrations/ — соседи по корню).
Не нашли ни там, ни там — падаем сразу, не дойдя до базы.

**Почему падаем, а не молчим.** 16.09.2026 запуск из репозитория напечатал
«готово: 0 новых, 0 уже были» и вышел с кодом 0, хотя не прочитал ни одного
файла: glob по несуществующему каталогу возвращает пустой список, и пустой
набор неотличим от «накатывать нечего». Накат, который молча делает ничего,
опаснее упавшего: следующий шаг работает с базой без своей таблицы.

Бутстрап на чистой базе не нужен вовсе: public.schema_migration пуста, и
первый же прогон накатывает все файлы по порядку с нуля — обычный путь.
Он нужен только на стенде, где часть файлов уже накатана руками ДО того, как
появился этот скрипт (наш случай 15.09.2026 с 001…006 — см.
deploy/bootstrap-schema-migration.sql): без бутстрапа первый прогон попробует
выполнить CREATE TABLE по уже существующим объектам и упадёт.

**Накат с чистого листа проверен 16.09.2026** (до этой даты здесь стояло
«не проверено, только рассуждение по коду»). На пустой базе moskollektor_f78,
созданной ради проверки Ф-78 и снесённой после неё, все восемь файлов
накатились по порядку с нуля: «готово: 8 новых, 0 уже были». Расширения
uuid-ossp, btree_gist и postgis ставят сами миграции, отдельного шага
это не требует.
"""

import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import asyncpg

_ЗДЕСЬ = Path(__file__).resolve().parent
# Каталог ищем в тех же двух местах, что и миграции: ./db/... рядом с пакетом
# (образ) и на уровень выше (репозиторий).
MIGRATIONS_DIR = next(
    (
        корень / "db" / "migrations"
        for корень in (_ЗДЕСЬ.parent, _ЗДЕСЬ.parent.parent)
        if (корень / "db" / "migrations").is_dir()
    ),
    None,
)
SEEDS_DIR = next(
    (
        корень / "db" / "seed"
        for корень in (_ЗДЕСЬ.parent, _ЗДЕСЬ.parent.parent)
        if (корень / "db" / "seed").is_dir()
    ),
    None,
)

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


def _seeds(dir_):
    """[(name, sql)] по возрастанию имени. Пустой набор — падаем, а не молчим.

    Причина та же, что у миграций выше: glob по пустому каталогу возвращает
    пустой список, и «каталог не тот» неотличим от «сидов вовсе нет». Сиды
    входят в образ строкой COPY db/seed (backend/Dockerfile), поэтому пустой
    каталог означает сломанную сборку, а не пустой справочник.
    """
    if dir_ is None:
        raise ValueError(
            "каталог db/seed не найден ни рядом с пакетом, ни на уровень выше"
        )
    files = [(path.name, path.read_text()) for path in sorted(dir_.glob("*.sql"))]
    if not files:
        raise ValueError(f"в {dir_} нет ни одного .sql — сиды обязаны ехать в образе")
    return files


async def main():
    if MIGRATIONS_DIR is None:
        raise SystemExit(
            f"каталог db/migrations не найден ни в {_ЗДЕСЬ.parent}, "
            f"ни в {_ЗДЕСЬ.parent.parent} — накатывать нечего и молчать об этом нельзя"
        )
    # Сиды читаем ДО соединения: битый образ должен упасть, не трогая базу.
    seeds = _seeds(SEEDS_DIR)
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text()
        files.append((path.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute(CREATE_JOURNAL)
        applied = dict(
            await conn.fetch("SELECT filename, sha256 FROM public.schema_migration")
        )
        pending = _pending(files, applied)
        for name, sql, sha256 in pending:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO public.schema_migration (filename, sha256) VALUES ($1, $2)",
                    name,
                    sha256,
                )
            print(f"{name}: накатан")
        print(f"готово: {len(pending)} новых, {len(files) - len(pending)} уже были")

        # Сиды — после всех миграций и заново каждый прогон (см. шапку файла).
        # Транзакция на файл, как у миграций: упавший сид не оставляет полсправочника.
        for name, sql in seeds:
            async with conn.transaction():
                await conn.execute(sql)
            print(f"{name}: сид накатан заново")
        print(f"сиды: {len(seeds)} файлов, все идемпотентные")
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

    # Сиды: каталога нет и каталог пустой — оба случая обязаны падать, а не
    # отдавать пустой список (та же ловушка, что и у миграций в шапке файла).
    try:
        _seeds(None)
    except ValueError:
        pass
    else:
        raise AssertionError("должен упасть без каталога db/seed")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _seeds(Path(tmp))
        except ValueError:
            pass
        else:
            raise AssertionError("должен упасть на пустом каталоге db/seed")
    if SEEDS_DIR is not None:
        assert _seeds(SEEDS_DIR), "сиды в db/seed не разобраны"


if __name__ == "__main__":
    _selfcheck()
    try:
        asyncio.run(main())
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
