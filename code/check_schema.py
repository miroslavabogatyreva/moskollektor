#!/usr/bin/env python3
"""Проверка пяти миграций db/migrations/001…005 до накатывания на базу.

Зачем. Postgres на машине разработчика может не стоять, а ошибка вида «таблица
ссылается на несуществующую» вылезет только при накатывании миграций — то есть
на вехе 1, когда исправлять дороже. Проверка читает SQL глазами регулярных
выражений и отвечает на девять вопросов:

  1. У каждой таблицы, представления и функции есть схема? Объект без префикса
     свалится в public и разъедется с тем, что обещает docs/HLD.md разд. 5.1.
  2. Каждый внешний ключ указывает на существующую таблицу?
  3. Нет ли двух таблиц с одинаковым полным именем?
  4. Каждая схема, которой пользуется файл, объявлена этим файлом или более
     ранней миграцией — по номеру, то есть по порядку накатывания? (Проверка
     «схема использована, но не объявлена нигде» была отдельным пунктом до
     17.09.2026 — MOS-116 убрал её: это частный случай той же проверки при
     declared_so_far пустом с самого начала, вторая находка на одном месте.)
  5. Порядок накатывания: файлы идут по возрастанию номера, и ни один REFERENCES
     или ALTER TABLE не смотрит на таблицу, которой к этому месту ещё нет.
  6. Типы совпадают: колонка ключа и колонка, на которую он смотрит, одного
     семейства (bigint против integer, text против uuid — это поломка).
  7. Колонки, похожие на внешний ключ (*_id, а также *_code/*_no/*_key, если
     есть таблица с таким именем), но без REFERENCES — либо в списке
     INTENTIONAL с причиной, либо это находка.
  8. Висячие таблицы, у которых нет ни одной связи ни в одну сторону, — либо
     в списке DANGLING_OK с причиной, либо находка.
  9. Партиционированные таблицы: ключ партиционирования входит в первичный ключ,
     иначе Postgres откажется создавать таблицу.

Это не замена накатыванию на настоящую базу. Это дешёвая проверка, которая
ловит самые частые поломки за полсекунды.

ЧЕГО ЭТОТ СКРИПТ НЕ ПРОВЕРЯЕТ, и это важнее списка выше. Он сверяет схему
с самой собой, а не с данными. Он не знает, чем заполнять NOT NULL, какие
значения бывают в колонке и за какой год пришли строки. **Зелёный вывод
означает «накатится», а не «загрузится».**

Три поломки, найденные 15.09.2026 на первой выгрузке заказчика, скрипт
не показал ни одной:
  * section_id integer NOT NULL на таблице показаний — привязки к участку
    не было ни у одного из 12 627 каналов, COPY отверг бы все 313 546 016 строк;
  * единственная партиция заведена за январь 2014 года, а данные начинаются
    с 2019-го — всё уехало бы в партицию default;
  * суточная свёртка считала события по кодам систем 'АКМ' и 'ОПС', которых
    в выгрузке нет ни разу, и молча возвращала нули на любых файлах.
Все три нашли люди и агенты, ни одну не нашёл скрипт. Та же мысль словами —
в CLAUDE.md, раздел «Где что лежит».

РЕШЕНИЕ MOS-114 (17.09.2026). Проверка 5 требовала CREATE SCHEMA в том же файле,
где схема используется, и падала на 017_fault_episodes.sql: он кладёт таблицы
в smvu, а CREATE SCHEMA smvu стоит в 004_events.sql. Правило накатывания это
разрешает — миграции идут по порядку, и к моменту 017 схема smvu уже есть.
Была развилка: дописать CREATE SCHEMA в 017 (самодостаточность файла, как это
уже сделано в 005, 008, 009, 012, 013) или ослабить проверку до «схема объявлена
этим файлом или любым более ранним». Первый путь сломал бы sha256 у уже
накатанного на стенде файла. Выбран второй: проверка 5 (теперь 4) смотрит
на схемы, накопленные по всем миграциям до текущей включительно.

РЕШЕНИЕ MOS-116 (17.09.2026). Три находки приёмки MOS-114/MOS-105.
Первая: RE_TABLE искал закрывающую скобку таблицы литералом «)» в начале
строки — однострочный CREATE TABLE был невидим целиком, а не просто хуже
разобран. Разбор таблиц переписан на поиск открывающей скобки регулярным
выражением и подсчёт скобок посимвольно (с учётом кавычек) до парной
закрывающей — это не зависит от переносов строк в принципе, а не только
для одного случая. По той же причине колонки внутри тела таблицы теперь
режутся по запятым верхнего уровня (split_clauses), а не по переносам строк:
однострочная и многострочная запись дают одинаковый разбор. Вторая: проверка
«схема использована, но не объявлена нигде» была подмножеством проверки
«схема объявлена этим файлом или более ранним» — при пустом declared_so_far
это одно и то же условие, проверка снята, номера пунктов ниже сдвинуты.
Третья: precision/recall из tp/fp/fn считались трижды (evaluate_alerts,
verdict, code/check_metrics.py) — вынесены в precision_recall() в
predictive_metrics.py, остальные два места её зовут.
"""

import re
import sys
import tempfile
from pathlib import Path

# Файлы лежат в db/migrations/, а не рядом со скриптом: проверять надо то,
# что накатывается. Порядок — по возрастанию номера, он же порядок накатывания:
# НСИ и ТОиР -> гео -> наряды -> события -> перекодировка.
MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


def migrations(root):
    """Имена миграций по возрастанию номера.

    Список не зашит намеренно. Раньше он стоял константой из пяти имён, и когда
    Q2.3 положит 006_smvu_ref.sql, проверка бы её не увидела и всё равно ответила
    «проблем нет» — то есть перестала бы проверять ровно то, ради чего написана.
    Пропажу файла она ловит громко (FileNotFoundError), лишний файл не ловила никак.

    Пустой каталог — отказ, а не пустая проверка. Промахнуться путём легче, чем
    сломать схему, а «таблиц: 0, проблем нет» выглядит как успех.
    """
    files = sorted(p.name for p in root.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        sys.exit(f"в {root} нет ни одной миграции — проверять нечего")
    return files

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
    "ref.explain_template":
        "ключ — имя признака из contracts/features.v1.yaml, а не строка в базе; ссылаться не на что",
    "ref.setpoint":
        "уставка описывает параметр среды (вода, метан, кислород), а параметры среды "
        "таблицей не заведены: канал знает свой тип датчика строкой. Ссылка на ОВ-46 "
        "стояла здесь по ошибке и снята 16.09.2026: связь канала с деревом объектов "
        "заказчик прислал, а тип датчика от неё не зависел никогда",
    "ref.norm_period":
        "норматив ТО привязан к системе Регламента словами («СКУД», «АКМ: АТ3-1»), "
        "а реестра оборудования у нас нет вовсе — это открытый вопрос Ф-84",
    "load.change_request":
        "заявка на правку НСИ ссылается на объект полиморфно (object_type + target_code)",
    "permit.permit_status_transition":
        "таблица правил для приложения; в журнале from_status бывает NULL, а такой ключ Postgres не проверяет",
    "ref.role_permission":
        "разрешения — данные (db/seed/rbac.sql), не связь: роли — фиксированный список "
        "через CHECK на role_code, отдельного справочника ролей решением Q4.1 нет",
    "feat.refresh_run":
        "журнал прогонов свёртки (023_refresh_run.sql): строка описывает запуск "
        "функции feat.refresh_section_daily, а не объект парка. Ссылаться ей не на "
        "что — свёртка обновляет сразу все участки за двое суток, и ключа, который "
        "назвал бы один из них, у прогона нет",
    "smvu.data_outage":
        "журнал окон тишины выгрузки по времени (017_fault_episodes.sql); с "
        "smvu.fault_episode связана пересечением интервалов (spans_outage), "
        "а не внешним ключом — ссылочной колонки нет ни у одной стороны",
}

NAME = r'[a-z_]+(?:\.[a-z_]+)?'
# Только начало CREATE TABLE: где заканчивается тело, ищет find_tables()
# посимвольным подсчётом скобок — регулярка для парной скобки не годится,
# ей нужен якорь вроде «)» в начале строки, а якоря на переносы строк
# зависеть не должны (MOS-116).
RE_TABLE_START = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(' + NAME + r')\s*\(', re.M)
RE_SCHEMA = re.compile(r'CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)', re.M)
RE_REF = re.compile(r'REFERENCES\s+(' + NAME + r')\s*(?:\(\s*([a-z_]+)\s*\))?')
RE_VIEW = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:RECURSIVE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(' + NAME + ')',
    re.M)
RE_FUNC = re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(' + NAME + r')\s*\(', re.M)
RE_ALTER_FK = re.compile(
    r'ALTER\s+TABLE\s+(' + NAME + r')\s+ADD\s+(?:CONSTRAINT\s+[a-z_]+\s+)?FOREIGN\s+KEY\s*'
    r'\(([a-z_]+)\)\s*REFERENCES\s+(' + NAME + r')\s*\(\s*([a-z_]+)\s*\)', re.S)
RE_COL = re.compile(r'^([a-z_]+)\s+((?:character varying|double precision|[a-z_]+)(?:\s*\([^)]*\))?)')
RE_PARTITION = re.compile(r'PARTITION\s+BY\s+(?:RANGE|LIST|HASH)\s*\(\s*([a-z_]+)\s*\)')

CONSTRAINT_WORDS = ("PRIMARY KEY", "UNIQUE", "CHECK", "EXCLUDE", "CONSTRAINT", "FOREIGN")

# Семейства типов: внутри семейства ключ работает, между семействами — нет.
FAMILY = {"bigserial": "bigint", "serial": "integer", "int": "integer",
          "int4": "integer", "int8": "bigint", "varchar": "text",
          "character varying": "text", "character": "char", "bpchar": "char"}


def strip_comments(sql):
    """Убирает -- комментарии: в них лежат примеры запросов, они не код.

    Кавычки учитываем. Раньше строка резалась по первому «--» без разбора, и литерал
    с двумя дефисами внутри — скажем, CHECK на формат тега или текст с длинным тире —
    обрубил бы определение таблицы молча, а проверка осталась бы зелёной. На сегодняшних
    пяти файлах схемы разницы между двумя способами нет (проверено сравнением), так что
    это защита на будущее, а не исправление текущей поломки.
    """
    out = []
    for line in sql.splitlines():
        in_quotes = False
        cut = len(line)
        i = 0
        while i < len(line):
            if line[i] == "'":
                in_quotes = not in_quotes
            elif not in_quotes and line.startswith("--", i):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def family(typ):
    base = typ.split("(")[0].strip().lower()
    return FAMILY.get(base, base)


def _matching_paren(sql, open_idx):
    """Индекс ')', парной '(' на open_idx. Кавычки учтены, вложенность тоже.

    Заменяет старый якорь «закрывающая скобка стоит в начале строки»: тот
    требовал определённой раскладки по строкам и не видел однострочный
    CREATE TABLE вовсе (MOS-116). Подсчёт скобок работает при любой раскладке.
    """
    depth = 0
    in_quotes = False
    i = open_idx
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValueError(f"незакрытая скобка, открыта на позиции {open_idx}")


def find_tables(sql):
    """Все CREATE TABLE в sql: (имя, тело, хвост после ')', позиция начала).

    Хвост — то, что стоит между ')' и ';' (например, PARTITION BY ...).
    """
    out = []
    for m in RE_TABLE_START.finditer(sql):
        open_idx = m.end() - 1
        close_idx = _matching_paren(sql, open_idx)
        semi = sql.index(";", close_idx)
        out.append((m.group(1), sql[m.end():close_idx], sql[close_idx + 1:semi], m.start()))
    return out


def split_clauses(body):
    """Тело CREATE TABLE на колонки и ограничения по запятым верхнего уровня.

    Не по переносам строк (MOS-116): однострочная и многострочная запись
    обязаны разбираться одинаково. Скобки (numeric(10,2)) и кавычки
    (CHECK (a IN ('x,y'))) учтены, чтобы запятая внутри них клаузу не резала.
    """
    clauses, depth, in_quotes, start = [], 0, False, 0
    for i, ch in enumerate(body):
        if ch == "'":
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                clauses.append(body[start:i])
                start = i + 1
    clauses.append(body[start:])
    return [c.strip() for c in clauses if c.strip()]


def parse_body(body):
    """Колонки таблицы: {имя: тип}, колонки PK и одиночные UNIQUE, ссылки колонок."""
    cols, pk, uniq, refs = {}, [], set(), []
    for line in split_clauses(body):
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
    """Читает миграции по возрастанию номера, отдаёт таблицы, схемы, ссылки и находки порядка."""
    tables, schemas, views, funcs = {}, set(), set(), set()
    links = []          # (таблица, колонка, цель, колонка цели, файл)
    order_problems = []
    created = set()
    for name in migrations(root):
        sql = strip_comments((root / name).read_text())
        schemas |= set(RE_SCHEMA.findall(sql))
        views |= set(RE_VIEW.findall(sql))
        funcs |= set(RE_FUNC.findall(sql))
        # События файла по смещению: CREATE TABLE добавляет таблицу,
        # REFERENCES и ALTER TABLE требуют, чтобы цель уже была.
        events = []
        for t, body, tail, pos in find_tables(sql):
            cols, pk, uniq, refs = parse_body(body)
            part = RE_PARTITION.search(tail)
            tables.setdefault(t, []).append(
                dict(file=name, cols=cols, pk=pk, uniq=uniq,
                     partition_key=part.group(1) if part else None))
            events.append((pos, "create", t))
            for col, tgt, tcol in refs:
                links.append((t, col, tgt, tcol, name))
                events.append((pos + 1, "ref", (t, tgt)))
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


def check(root=MIGRATIONS):
    tables, schemas, views, funcs, links, order_problems = scan(root)
    problems = list(order_problems)

    # 1. объекты без схемы
    for kind, names in (("таблица", tables), ("представление", views), ("функция", funcs)):
        for t in sorted(n for n in names if "." not in n):
            problems.append(f"{kind} без схемы: {t}")

    # 2. внешние ключи в никуда
    for t, col, tgt, tcol, where in links:
        if tgt not in tables:
            problems.append(f"внешний ключ в никуда: {t}.{col} REFERENCES {tgt} (в {where})")

    # 3. дубликаты имён
    for t, defs in tables.items():
        if len(defs) > 1:
            problems.append(f"таблица {t} объявлена дважды: "
                            f"{', '.join(d['file'] for d in defs)}")

    # 4. каждая схема, которой пользуется файл, объявлена им самим или более
    #    ранней миграцией. Миграции накатываются по одной и по порядку: файл,
    #    который кладёт таблицу в схему, не объявленную нигде до него включительно,
    #    упадёт при накатывании. Схема, объявленная раньше (004_events.sql —
    #    smvu, например), к моменту более позднего файла (017) уже есть в базе —
    #    решение MOS-114 в шапке файла. Схема, не объявленная нигде вообще, —
    #    частный случай (declared_so_far пустое даже в конце) и отдельной
    #    проверки не требует — MOS-116 снял старую проверку 2, которая делала
    #    ровно это же вторым способом.
    declared_so_far = set()
    for name in migrations(root):
        sql = strip_comments((root / name).read_text())
        declared_so_far |= set(RE_SCHEMA.findall(sql))
        used_here = {t.split(".")[0] for t, _, _, _ in find_tables(sql) if "." in t}
        for s in sorted(used_here - declared_so_far):
            problems.append(f"{name}: кладёт таблицы в схему {s}, но не объявляет "
                            f"её ни сам, ни более ранняя миграция")

    # 6. типы по обе стороны ключа — одного семейства
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

    # 7. колонки, похожие на ключ, без ключа
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

    # 8. висячие таблицы
    connected = {t for t, _, tgt, _, _ in links for t in (t, tgt)}
    for t in sorted(set(tables) - connected):
        if t not in DANGLING_OK:
            problems.append(f"таблица без единой связи: {t}")
    for t in DANGLING_OK:
        if t in connected:
            problems.append(f"DANGLING_OK устарел: {t} уже связана, убрать из списка")

    # 9. ключ партиционирования входит в первичный ключ
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
    # MOS-116: та же ALTER FK в одну строку — раньше литеральные пробелы между
    # ключевыми словами требовали ровно одной раскладки, теперь \s+ везде.
    one_line_fk = RE_ALTER_FK.search(
        "ALTER TABLE a.b ADD CONSTRAINT x FOREIGN KEY (c) REFERENCES d.e(f);")
    assert one_line_fk and one_line_fk.groups() == m.groups()
    assert RE_PARTITION.search(" PARTITION BY RANGE (read_time)").group(1) == "read_time"

    # MOS-116: find_tables()/split_clauses не должны зависеть от переносов строк.
    assert find_tables("CREATE TABLE a.b (id integer PRIMARY KEY);")[0][:2] == (
        "a.b", "id integer PRIMARY KEY")
    assert find_tables("CREATE TABLE a.b (\n    id integer PRIMARY KEY\n);")[0][:2] == (
        "a.b", "\n    id integer PRIMARY KEY\n")
    # numeric(10,2) внутри клаузы — запятая не режет колонку пополам.
    assert split_clauses("price numeric(10,2) NOT NULL, id integer") == (
        ["price numeric(10,2) NOT NULL", "id integer"])

    # Комментарий режется, а литерал с двумя дефисами внутри кавычек — нет.
    # Без этого определение таблицы обрубилось бы молча, а проверка осталась зелёной.
    assert strip_comments("a text,  -- пояснение") == "a text,  "
    assert strip_comments("CHECK (tag ~ '^[0-9]+--[0-9]+$'),  -- формат тега") == \
        "CHECK (tag ~ '^[0-9]+--[0-9]+$'),  "
    assert strip_comments("name text DEFAULT 'тире -- внутри'") == \
        "name text DEFAULT 'тире -- внутри'"
    assert strip_comments("-- строка целиком комментарий") == ""

    # MOS-114: схема, объявленная в более ранней миграции, — не находка (017/smvu
    # из 004); схема, не объявленная нигде, — по-прежнему находка. Проверка проверки:
    # без второй ветки правка проверки 5 могла бы просто перестать что-либо ловить.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "001_a.sql").write_text("CREATE SCHEMA IF NOT EXISTS foo;\n")
        (root / "002_b.sql").write_text("CREATE TABLE foo.bar (\n    id integer PRIMARY KEY\n);\n")
        _, _, ok_problems = check(root)
        assert not any("foo" in p and "не объявляет" in p for p in ok_problems), ok_problems

        (root / "003_c.sql").write_text("CREATE TABLE baz.qux (\n    id integer PRIMARY KEY\n);\n")
        _, _, broken_problems = check(root)
        assert any("baz" in p and "не объявляет" in p for p in broken_problems), broken_problems

    # MOS-116: битый образец в двух написаниях — однострочном и многострочном —
    # обязан давать одну и ту же находку и один и тот же счётчик таблиц. Раньше
    # RE_TABLE требовал ')' в начале строки и однострочный CREATE TABLE не видел
    # вовсе: 98 таблиц вместо 99, находок 0 вместо 3.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "900_bad.sql").write_text("CREATE TABLE nowhere.t (id integer PRIMARY KEY);\n")
        tables_one, _, problems_one = check(root)

        (root / "900_bad.sql").write_text(
            "CREATE TABLE nowhere.t (\n    id integer PRIMARY KEY\n);\n")
        tables_multi, _, problems_multi = check(root)

        assert len(tables_one) == len(tables_multi) == 1, (tables_one, tables_multi)
        assert problems_one == problems_multi, (problems_one, problems_multi)
        # Проверка 2 снята: старое сообщение проверка 4 не повторяет — она уже
        # нашла то же самое своим текстом («не объявляет её ни сам, ни более
        # ранняя миграция»), а не старым «используется, но нет CREATE SCHEMA».
        assert not any("используется, но нет CREATE SCHEMA" in p for p in problems_one), \
            problems_one
        assert any("nowhere" in p and "не объявляет" in p for p in problems_one), problems_one


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
