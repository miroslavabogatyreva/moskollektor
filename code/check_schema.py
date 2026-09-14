#!/usr/bin/env python3
"""Проверка пяти файлов схемы до накатывания на базу.

Зачем. Postgres на машине разработчика может не стоять, а ошибка вида «таблица
ссылается на несуществующую» вылезет только при накатывании миграций — то есть
на вехе 1, когда исправлять дороже. Проверка читает SQL глазами регулярных
выражений и отвечает на девять вопросов:

  1. У каждой таблицы, представления и функции есть схема? Объект без префикса
     свалится в public и разъедется с тем, что обещает docs/HLD.md разд. 5.1.
  2. Каждая схема объявлена через CREATE SCHEMA до первого её использования?
  3. Каждый внешний ключ указывает на существующую таблицу?
  4. Нет ли двух таблиц с одинаковым полным именем?
  5. Каждый файл объявляет схемы, которыми сам пользуется?
  6. Порядок накатывания: файлы идут по списку FILES, и ни один REFERENCES
     или ALTER TABLE не смотрит на таблицу, которой к этому месту ещё нет.
  7. Типы совпадают: колонка ключа и колонка, на которую он смотрит, одного
     семейства (bigint против integer, text против uuid — это поломка).
  8. Колонки, похожие на внешний ключ (*_id, а также *_code/*_no/*_key, если
     есть таблица с таким именем), но без REFERENCES — либо в списке
     INTENTIONAL с причиной, либо это находка.
  9. Висячие таблицы, у которых нет ни одной связи ни в одну сторону, — либо
     в списке DANGLING_OK с причиной, либо находка.
 10. Партиционированные таблицы: ключ партиционирования входит в первичный ключ,
     иначе Postgres откажется создавать таблицу.

Это не замена накатыванию на настоящую базу. Это дешёвая проверка, которая
ловит самые частые поломки за полсекунды.
"""

import re
import sys
from pathlib import Path

# Порядок накатывания. Он же порядок миграций из docs/HLD.md разд. 5.4,
# свёрнутый до пяти файлов: НСИ и ТОиР -> гео -> наряды -> события -> перекодировка.
FILES = ["schema_assets.sql", "schema_geo.sql", "schema_permits.sql",
         "schema_events.sql", "schema_xref.sql"]

# Схемы, которые обещает docs/HLD.md разд. 5.1
EXPECTED = {"smvu", "feat", "pred", "ref", "asset", "maint", "load", "geo", "permit"}

# Колонки, похожие на ключ, у которых ключа нет намеренно. Причина — здесь и в SQL.
INTENTIONAL = {
    "geo.geo_object.smvu_id":
        "реестр объектов грузится раньше выгрузки СМВУ; участок связан через ref.object_xref",
    "geo.geo_unresolved.smvu_id":
        "черновая строка до привязки, датчика в справочнике может ещё не быть",
}

# Таблицы без единой связи, и почему это нормально.
DANGLING_OK = {
    "ref.catalog_code":
        "коды ISO 14224 уникальны только внутри группы, приложение проверяет тройку",
    "ref.catalog_profile":
        "то же: профиль — набор строк (code, catalog, code_group), одним столбцом не сослаться",
    "load.change_request":
        "заявка на правку НСИ ссылается на объект полиморфно (object_type + target_code)",
    "permit.permit_status_transition":
        "таблица правил для приложения; в журнале from_status бывает NULL, а такой ключ Postgres не проверяет",
}

NAME = r'[a-z_]+(?:\.[a-z_]+)?'
RE_TABLE = re.compile(r'^CREATE TABLE (?:IF NOT EXISTS )?(' + NAME + r')\s*\((.*?)^\)([^;]*);',
                      re.M | re.S)
RE_SCHEMA = re.compile(r'^CREATE SCHEMA (?:IF NOT EXISTS )?([a-z_]+)', re.M)
RE_REF = re.compile(r'REFERENCES\s+(' + NAME + r')\s*(?:\(\s*([a-z_]+)\s*\))?')
RE_VIEW = re.compile(r'^CREATE (?:OR REPLACE )?(?:RECURSIVE )?(?:MATERIALIZED )?VIEW (' + NAME + ')',
                     re.M)
RE_FUNC = re.compile(r'^CREATE (?:OR REPLACE )?FUNCTION (' + NAME + r')\s*\(', re.M)
RE_ALTER_FK = re.compile(
    r'ALTER TABLE (' + NAME + r')\s+ADD (?:CONSTRAINT [a-z_]+ )?FOREIGN KEY \(([a-z_]+)\)\s*'
    r'REFERENCES\s+(' + NAME + r')\s*\(\s*([a-z_]+)\s*\)', re.S)
RE_COL = re.compile(r'^([a-z_]+)\s+((?:character varying|double precision|[a-z_]+)(?:\s*\([^)]*\))?)')
RE_PARTITION = re.compile(r'PARTITION BY (?:RANGE|LIST|HASH)\s*\(\s*([a-z_]+)\s*\)')

CONSTRAINT_WORDS = ("PRIMARY KEY", "UNIQUE", "CHECK", "EXCLUDE", "CONSTRAINT", "FOREIGN")

# Семейства типов: внутри семейства ключ работает, между семействами — нет.
FAMILY = {"bigserial": "bigint", "serial": "integer", "int": "integer",
          "int4": "integer", "int8": "bigint", "varchar": "text",
          "character varying": "text", "character": "char", "bpchar": "char"}


def strip_comments(sql):
    """Убирает -- комментарии: в них лежат примеры запросов, они не код."""
    return "\n".join(ln.split("--")[0] for ln in sql.splitlines())


def family(typ):
    base = typ.split("(")[0].strip().lower()
    return FAMILY.get(base, base)


def parse_body(body):
    """Колонки таблицы: {имя: тип}, колонки PK и одиночные UNIQUE, ссылки колонок."""
    cols, pk, uniq, refs = {}, [], set(), []
    for line in body.split("\n"):
        line = line.strip().rstrip(",")
        if not line:
            continue
        up = line.upper()
        if up.startswith(CONSTRAINT_WORDS):
            m = re.search(r'PRIMARY KEY\s*\(([^)]*)\)', line)
            if m:
                pk = [c.strip() for c in m.group(1).split(",")]
            m = re.match(r'UNIQUE(?: NULLS NOT DISTINCT)?\s*\(([^)]*)\)', line)
            if m and "," not in m.group(1):
                uniq.add(m.group(1).strip())
            continue
        m = RE_COL.match(line)
        if not m:
            continue
        col, typ = m.group(1), m.group(2)
        cols[col] = typ
        if "PRIMARY KEY" in line:
            pk = [col]
        if "UNIQUE" in line:
            uniq.add(col)
        for tgt, tcol in RE_REF.findall(line):
            refs.append((col, tgt, tcol or None))
    return cols, pk, uniq, refs


def scan(root):
    """Читает файлы в порядке FILES, отдаёт таблицы, схемы, ссылки и находки порядка."""
    tables, schemas, views, funcs = {}, set(), set(), set()
    links = []          # (таблица, колонка, цель, колонка цели, файл)
    order_problems = []
    created = set()
    for name in FILES:
        sql = strip_comments((root / name).read_text())
        schemas |= set(RE_SCHEMA.findall(sql))
        views |= set(RE_VIEW.findall(sql))
        funcs |= set(RE_FUNC.findall(sql))
        # События файла по смещению: CREATE TABLE добавляет таблицу,
        # REFERENCES и ALTER TABLE требуют, чтобы цель уже была.
        events = []
        for m in RE_TABLE.finditer(sql):
            t, body, tail = m.group(1), m.group(2), m.group(3)
            cols, pk, uniq, refs = parse_body(body)
            part = RE_PARTITION.search(tail)
            tables.setdefault(t, []).append(
                dict(file=name, cols=cols, pk=pk, uniq=uniq,
                     partition_key=part.group(1) if part else None))
            events.append((m.start(), "create", t))
            for col, tgt, tcol in refs:
                links.append((t, col, tgt, tcol, name))
                events.append((m.start() + 1, "ref", (t, tgt)))
        for m in RE_ALTER_FK.finditer(sql):
            t, col, tgt, tcol = m.groups()
            links.append((t, col, tgt, tcol, name))
            events.append((m.start(), "alter", (t, tgt)))
        for _, kind, payload in sorted(events, key=lambda e: e[0]):
            if kind == "create":
                created.add(payload)
                continue
            t, tgt = payload
            if kind == "alter" and t not in created:
                order_problems.append(f"{name}: ALTER TABLE {t}, а таблицы ещё нет")
            if tgt not in created and tgt != t:
                order_problems.append(
                    f"{name}: {t} ссылается на {tgt}, которой к этому месту ещё нет")
    return tables, schemas, views, funcs, links, order_problems


def check(root=Path(__file__).parent):
    tables, schemas, views, funcs, links, order_problems = scan(root)
    problems = list(order_problems)

    # 1. объекты без схемы
    for kind, names in (("таблица", tables), ("представление", views), ("функция", funcs)):
        for t in sorted(n for n in names if "." not in n):
            problems.append(f"{kind} без схемы: {t}")

    # 2. схемы использованы, но не объявлены
    used = {t.split(".")[0] for t in tables if "." in t}
    for s in sorted(used - schemas):
        problems.append(f"схема {s} используется, но нет CREATE SCHEMA {s}")

    # 3. внешние ключи в никуда
    for t, col, tgt, tcol, where in links:
        if tgt not in tables:
            problems.append(f"внешний ключ в никуда: {t}.{col} REFERENCES {tgt} (в {where})")

    # 4. дубликаты имён
    for t, defs in tables.items():
        if len(defs) > 1:
            problems.append(f"таблица {t} объявлена дважды: "
                            f"{', '.join(d['file'] for d in defs)}")

    # 5. каждый файл объявляет схемы, которыми сам пользуется.
    #    Миграции накатываются по одной и по порядку: файл, который кладёт
    #    таблицу в чужую схему, упадёт, если тот файл ещё не накатан.
    for name in FILES:
        sql = strip_comments((root / name).read_text())
        own = set(RE_SCHEMA.findall(sql))
        used_here = {x.split(".")[0] for x, _, _ in RE_TABLE.findall(sql) if "." in x}
        for s in sorted(used_here - own):
            problems.append(f"{name}: кладёт таблицы в схему {s}, но не объявляет её")

    # 7. типы по обе стороны ключа — одного семейства
    for t, col, tgt, tcol, where in links:
        if t not in tables or tgt not in tables:
            continue
        src, dst = tables[t][0], tables[tgt][0]
        tcol = tcol or (dst["pk"][0] if len(dst["pk"]) == 1 else None)
        if tcol is None:
            problems.append(f"{t}.{col} -> {tgt}: у цели составной PK, колонка не указана")
            continue
        if tcol not in dst["cols"]:
            problems.append(f"{t}.{col} -> {tgt}.{tcol}: такой колонки нет")
            continue
        a, b = family(src["cols"].get(col, "?")), family(dst["cols"][tcol])
        if a != b:
            problems.append(f"типы не совпадают: {t}.{col} {a} -> {tgt}.{tcol} {b}")

    # 8. колонки, похожие на ключ, без ключа
    linked = {(t, col) for t, col, _, _, _ in links}
    basenames = {t.split(".")[-1] for t in tables}
    for t, defs in tables.items():
        d = defs[0]
        for col in d["cols"]:
            if (t, col) in linked or col in d["pk"] or col in d["uniq"]:
                continue
            prefix, _, suffix = col.rpartition("_")
            looks = suffix == "id" or (suffix in ("code", "no", "key") and prefix in basenames)
            if looks and f"{t}.{col}" not in INTENTIONAL:
                problems.append(f"похоже на внешний ключ, но ключа нет: {t}.{col}")
    for key in INTENTIONAL:
        t, col = key.rsplit(".", 1)
        if t not in tables or col not in tables[t][0]["cols"]:
            problems.append(f"INTENTIONAL ссылается на несуществующую колонку {key}")

    # 9. висячие таблицы
    connected = {t for t, _, tgt, _, _ in links for t in (t, tgt)}
    for t in sorted(set(tables) - connected):
        if t not in DANGLING_OK:
            problems.append(f"таблица без единой связи: {t}")
    for t in DANGLING_OK:
        if t in connected:
            problems.append(f"DANGLING_OK устарел: {t} уже связана, убрать из списка")

    # 10. ключ партиционирования входит в первичный ключ
    for t, defs in tables.items():
        d = defs[0]
        if d["partition_key"] and d["partition_key"] not in d["pk"]:
            problems.append(f"{t}: партиционирована по {d['partition_key']}, "
                            f"а в PRIMARY KEY его нет ({', '.join(d['pk']) or 'PK нет'})")

    return tables, schemas, sorted(set(problems))


def _selfcheck():
    """Скрипт обязан ловить то, ради чего написан: подсовываем сломанный SQL."""
    cols, pk, uniq, refs = parse_body("""
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        section_id integer NOT NULL,
        run_id bigint NOT NULL REFERENCES pred.run,
        code varchar(4) NOT NULL UNIQUE,
        CHECK (id > 0)""")
    assert pk == ["id"] and uniq == {"code"} and refs == [("run_id", "pred.run", None)], (pk, uniq, refs)
    assert family("bigserial") == "bigint" and family("varchar(30)") == "text" != family("char(2)")
    m = RE_ALTER_FK.search("ALTER TABLE a.b\n    ADD CONSTRAINT x FOREIGN KEY (c) REFERENCES d.e(f);")
    assert m and m.groups() == ("a.b", "c", "d.e", "f")
    assert RE_PARTITION.search(" PARTITION BY RANGE (event_time)").group(1) == "event_time"


def main():
    _selfcheck()
    tables, schemas, problems = check()
    print(f"таблиц: {len(tables)}, схем объявлено: {len(schemas)}")
    print(f"схемы: {' '.join(sorted(schemas))}")
    missing = EXPECTED - schemas
    if missing:
        print(f"не объявлены из обещанных девяти: {' '.join(sorted(missing))}")
    if problems:
        print(f"\nнайдено проблем: {len(problems)}")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nпроблем нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
