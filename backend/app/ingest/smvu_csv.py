#!/usr/bin/env python3
"""Заливка выгрузки СМВУ в PostgreSQL: два справочника и журнал показаний.

Переехал сюда из `code/load_smvu.py` 15.09.2026 задачей Q2.4 и дорос до продуктивного:
прототип умел только положить файл в staging, а разбор в `smvu.reading`, заглушки
каналов и отчёт о качестве оставлял на потом. Здесь они есть.

Что заливаем и в каком порядке — порядок обязателен, его держат внешние ключи:

    1. dataset/справочник_объектов_диспетчер.csv  -> smvu.object_tree     95 строк
    2. dataset/справочник_каналов_датчиков.csv    -> ref.object_xref + smvu.channel
                                                                      11 485 строк
    3. dataset/ext-journal-YYYY.csv (восемь штук) -> smvu.reading  313 546 016 строк

Справочник каналов даёт участок: `code` разбора живёт рядом, в tag_to_section.py.
Пара «коллектор, пикет» («847:106») становится строкой ref.object_xref, её section_id
копируется в smvu.channel.section_id, а оттуда — в каждую строку показаний.

**Шестая колонка справочника каналов, `ид_объект`.** Заказчик прислал её 16.09.2026
в выгрузке от 15.09.2026 и этим закрыл ОВ-46: до неё канал с деревом объектов не был
связан ничем. Колонка заполнена у всех 11 485 каналов и едет в smvu.channel.object_id
(миграция 018). Выгрузку от 09.09.2026, где колонки нет, загрузчик по-прежнему берёт:
object_id тогда остаётся NULL, и об этом печатается строка, а не падение, — иначе
старый файл перестал бы грузиться из-за колонки, которой в нём и не могло быть.
Участок при этом не меняется: object_id отвечает на другой вопрос, под каким узлом
дерева диспетчер ищет канал, и одно из другого не выводится (разбор в 018).

**Чанк вместо общего staging.** Прототип клал в staging весь архив и разбирал типы
после. На 313 млн строк это 40 ГБ text-таблицы вдобавок к 30 ГБ самих показаний,
и на стенде столько места нет. Здесь staging живёт по 500 тыс. строк: COPY в
типизированную load.smvu_chunk, оттуда INSERT ... SELECT в reading, TRUNCATE, дальше.
Файлы можно лить несколькими процессами сразу: --slot даёт каждому свою чанк-таблицу,
а годы ложатся в разные месячные партиции и друг другу не мешают.
Пик — полгигабайта, и та лежит в кэше.

**Почему разбор типов вернулся в Python.** У чанка есть цена: INSERT упадёт целиком,
если в нём хоть одна битая метка времени, и вместе с ней пропадут 499 999 хороших
строк. Поэтому строку разбирает Python — битая не доходит до базы, а попадает
в отчёт со своим номером в файле.

**Зачем тогда чанк в базе, если строки уже разобраны.** Из-за дублей. COPY не умеет
ON CONFLICT, а дубли в выгрузке возможны: ext-journal-2025.csv склеен из двух выгрузок
встык, и если их периоды перекрываются, одно и то же ид_события приедет дважды.
Прямой COPY в reading упал бы на первом таком, а INSERT ... SELECT из чанка
с ON CONFLICT DO NOTHING считает дубли и продолжает.

**Заглушки каналов.** 1 143 канала есть в журнале и нет в справочнике заказчика —
снятое оборудование, чью историю нам всё равно прислали. Перед вставкой чанка
заводим на них строку smvu.channel c is_stub = true, иначе внешний ключ отверг бы
9,4 млн строк (3 % истории). Список заранее не нужен: ищем их по самому чанку.

**Три вторичных индекса на время заливки снимаем.** Полные btree на 313 млн строк
превращают вставку в перекладывание индекса. Четыре остальных остаются: PK ловит
дубли (ради него всё и затевалось), BRIN почти ничего не стоит, а `reading_fault_idx`
частичный — под условие попадает 0,48 % строк. Создаём снятое обратно после
последнего файла; упал прогон на середине — индексы остались снятыми, и отчёт
об этом говорит.

Запуск (из корня репозитория, справочники и журнал за один проход):

    PYTHONPATH=backend .venv/bin/python -m app.ingest.smvu_csv \
        --dsn "postgresql://moskollektor:ПАРОЛЬ@127.0.0.1:5432/moskollektor" \
        --objects dataset/справочник_объектов_диспетчер.csv \
        --channels dataset/справочник_каналов_датчиков.csv \
        dataset/ext-journal-*.csv

    PYTHONPATH=backend .venv/bin/python -m app.ingest.smvu_csv --selfcheck
"""

import argparse
import asyncio
import csv
import glob
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from .channel_place import дозаполнить_место, есть_место
from .synthetic_geometry import нарисовать_геометрию
from .kind_names import canon, canon_map
from .tag_to_section import collector_of, location_kind, section_key

# Пояс заказчик не назвал (ОВ-48). Ставим московский и пишем это в отчёт:
# среди значений встречается «01.01.1970 03:00:00» — нулевая эпоха, сдвинутая на три часа.
MSK = ZoneInfo("Europe/Moscow")

CHUNK = 500_000

# Ключ — нормализованный заголовок из файла, значение — поле записи.
# Незнакомый заголовок роняет разбор с явной ошибкой: лучше падение, чем сдвиг колонок.
HEADER_MAP = {
    "ид_события": "journal_id", "ид события": "journal_id",
    "ид_канала_данных": "channel_id", "ид канала данных": "channel_id",
    "дата": "day_s",
    "время": "time_s",
    "тревожное": "is_alarm_s",
    "значение_датчика": "value_s", "значение датчика": "value_s",
    # Приложение 1 ТЗ показывало другую форму того же журнала. Если заказчик привезёт
    # на приёмку её, а не то, что прислал в сентябре, загрузчик должен принять и её.
    "ид записи журнала": "journal_id",
    "ид типа канала данных": "channel_type_s",
    "текущее значение": "value_s",
    "дата записи": "day_s",
}

FIELDS = ["journal_id", "channel_id", "day_s", "time_s", "is_alarm_s", "value_s"]

# Как заказчик пишет истину и ложь. Файлы 2019 года — f/t, пример 2026 года — false/true.
TRUE_WORDS = {"t", "true", "1", "да", "истина"}
FALSE_WORDS = {"f", "false", "0", "нет", "ложь"}

CHUNK_COLUMNS = ["journal_id", "read_time", "channel_id", "is_alarm", "value_text", "value_num"]

CHUNK_DDL = """
CREATE UNLOGGED TABLE IF NOT EXISTS load.{t} (
    journal_id bigint,
    read_time  timestamptz,
    channel_id integer,
    is_alarm   boolean,
    value_text text,
    value_num  real
)
"""

# Канал из журнала, которого нет в справочнике заказчика. is_active = false:
# это снятое с эксплуатации оборудование, живым его показывать нельзя.
NEW_STUBS = """
INSERT INTO smvu.channel (channel_id, is_stub, is_active)
SELECT DISTINCT k.channel_id, true, false
  FROM load.{t} k
 WHERE NOT EXISTS (SELECT 1 FROM smvu.channel c WHERE c.channel_id = k.channel_id)
ON CONFLICT (channel_id) DO NOTHING
"""

INSERT_CHUNK = """
INSERT INTO smvu.reading
    (journal_id, read_time, channel_id, section_id, is_alarm, value_text, value_num, source_batch)
SELECT k.journal_id, k.read_time, k.channel_id, c.section_id,
       k.is_alarm, k.value_text, k.value_num, $1
  FROM load.{t} k
  JOIN smvu.channel c USING (channel_id)
ON CONFLICT DO NOTHING
"""

# Три вторичных индекса из db/migrations/004_events.sql, разделы 3.2-3.4. Текст
# обязан совпадать с миграцией дословно: пересозданный иначе индекс разойдётся
# со схемой чистого стенда, и планы запросов на приёмке будут другими.
SECONDARY_INDEXES = {
    "reading_channel_time_idx":
        "CREATE INDEX reading_channel_time_idx ON smvu.reading (channel_id, read_time DESC)",
    "reading_section_time_idx":
        "CREATE INDEX reading_section_time_idx ON smvu.reading (section_id, read_time DESC)",
    "reading_alarm_idx":
        "CREATE INDEX reading_alarm_idx ON smvu.reading (section_id, read_time DESC) WHERE is_alarm",
}


def norm_header(h):
    return str(h).strip().lower().replace("ё", "е").replace("\n", " ")


def map_headers(row):
    """Список полей записи по заголовку файла. None — колонку игнорируем."""
    out = []
    for h in row:
        key = norm_header(h)
        if key in HEADER_MAP:
            out.append(HEADER_MAP[key])
        elif key == "" or key.startswith("unnamed"):
            out.append(None)
        else:
            raise ValueError(f"незнакомый заголовок колонки: {h!r}")
    missing = {"journal_id", "channel_id", "day_s"} - set(out)
    if missing:
        raise ValueError(f"в файле нет обязательных колонок: {sorted(missing)}")
    return out


def parse_bool(s):
    """Флаг тревоги из любого написания. None — значение непонятно."""
    if s is None:
        return None
    key = str(s).strip().lower()
    if key in TRUE_WORDS:
        return True
    if key in FALSE_WORDS:
        return False
    return None


def parse_number(s):
    """Число из значения датчика, или None.

    Значение приходит текстом и смешивает три вещи: числа («28», «0.01»), состояния
    («Норма», «Обнаружено движение») и «01.01.1970 03:00:00» — маркер состояния
    охраны (MOS-225), а не пропуск или отказ. None здесь означает только «не число».
    Замер на первых 2 млн строк файла за 2019 год: числами разбираются 47,1 %,
    остальные 52,9 % числами не являются и обязаны лечь в value_text, а не потеряться.
    """
    if s is None:
        return None
    text = str(s).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_time(day_s, time_s):
    """Дата и время двумя колонками -> момент с московским поясом. None — не разобрано.

    Пояс проставляем явно: в самой выгрузке его нет ни колонкой, ни суффиксом,
    и без явного указания Postgres взял бы пояс сессии — то есть значение
    менялось бы от того, кто запустил загрузку.
    """
    if not day_s:
        return None
    try:
        return datetime.fromisoformat(f"{day_s.strip()}T{(time_s or '00:00:00').strip()}") \
            .replace(tzinfo=MSK)
    except ValueError:
        return None


def rows_from_file(path):
    """Генератор (номер строки в файле, запись для чанка, причина отказа).

    Запись — кортеж в порядке CHUNK_COLUMNS. Причина заполнена, когда строка
    не годится: тогда запись None, и строка попадает только в отчёт о качестве.
    """
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                return
            yield from _rows(reader, header, start=2)
    else:
        from python_calamine import CalamineWorkbook

        wb = CalamineWorkbook.from_path(path)
        data = wb.get_sheet_by_index(0).to_python(skip_empty_area=False)
        if not data:
            return
        yield from _rows(iter(data[1:]), data[0], start=2)


def _rows(reader, header, start):
    order = map_headers(header)
    idx = {name: i for i, name in enumerate(order) if name}
    header_norm = [norm_header(h) for h in header]
    width = len(header)
    for n, raw in enumerate(reader, start=start):
        # Ловушка: две выгрузки склеены встык, заголовок повторяется внутри файла.
        # В ext-journal-2025.csv он стоит на 26 140 585-й строке. COPY ... HEADER true
        # пропускает ровно одну строку, второй заголовок приехал бы как данные.
        if [norm_header(c) for c in raw] == header_norm:
            yield n, None, "повторный заголовок"
            continue
        if len(raw) < width:
            yield n, None, "короткая строка"
            continue
        get = lambda name: str(raw[idx[name]]) if name in idx else None  # noqa: E731
        try:
            journal_id = int(get("journal_id"))
            channel_id = int(get("channel_id"))
        except (TypeError, ValueError):
            yield n, None, "ид события или канала не число"
            continue
        read_time = parse_time(get("day_s"), get("time_s"))
        if read_time is None:
            yield n, None, "дата или время не разобраны"
            continue
        is_alarm = parse_bool(get("is_alarm_s"))
        if is_alarm is None:
            # NOT NULL в схеме. Подставить false — значит тихо сочинить данные:
            # пропущенная тревога стоит дороже отброшенной строки.
            yield n, None, "флаг тревоги не разобран"
            continue
        value_text = get("value_s")
        yield n, (journal_id, read_time, channel_id, is_alarm,
                  value_text, parse_number(value_text)), None


async def load_objects(conn, path):
    """Дерево диспетчерских объектов: 95 строк, три уровня.

    Уже залитый справочник не трогаем: команда из инструкции должна переживать
    повторный запуск, а COPY на существующем ключе упал бы.
    """
    if await conn.fetchval("SELECT count(*) FROM smvu.object_tree"):
        print("объекты: уже залиты, пропускаю")
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    ids = {int(r["ид_объект"]) for r in rows}
    orphan = 0
    recs = []
    for r in rows:
        parent = int(r["родитель"]) if r["родитель"] else None
        # В выгрузке корень (район, ид_объект 5773) ссылается на 3831, которого в файле
        # нет. Обнуляем ссылку: иначе ключ отверг бы корень, а с ним и всё дерево.
        if parent is not None and parent not in ids:
            parent, orphan = None, orphan + 1
        recs.append((int(r["ид_объект"]), int(r["иерархия_уровень"]), parent,
                     r["вид_объекта"], r["диспетчерское_название_объекта"]))
    # По уровню: ссылка на родителя проверяется сразу, значит родитель уже должен лежать.
    recs.sort(key=lambda t: t[1])
    await conn.executemany(
        "INSERT INTO smvu.object_tree (object_id, level, parent_id, kind, name) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (object_id) DO NOTHING", recs)
    print(f"объекты: {len(recs)} строк, ссылок на родителя вне файла обнулено {orphan}")
    return len(recs)


async def записать_чужие_типы(conn, path, чужие, каналов):
    """Типы вне справочника — в отчёт о качестве, по строке на тип.

    Строка приёмки Ф-78 требует четырёх вещей разом: канал с незнакомым типом
    загружен, помечен, назван в отчёте, и заливка при этом не прервалась.
    Пакет заводим только когда есть что записать: чистый справочник не должен
    плодить пустые строки отчёта.
    """
    if not чужие:
        print("    типы датчика и системы: все нашлись в справочниках")
        return 0
    пакет = await conn.fetchval(
        "INSERT INTO load.batch (object_name, source_file, tool, planned_rows, "
        "loaded_rows, failed_rows, stage, finished_at) "
        "VALUES ('Справочник каналов СМВУ', $1, 'smvu_csv', $2, $3, $4, "
        "'production', now()) RETURNING id",
        path, каналов, каналов, sum(len(v) for v in чужие.values()))
    for (что, значение), каналы in sorted(чужие.items()):
        await conn.execute(
            "INSERT INTO load.error (batch_id, source_key, rule_code, severity, "
            "message, payload) VALUES ($1, $2, 'FK_MISSING', 'warning', $3, "
            "jsonb_build_object('каналов', $4::int, 'ид_каналов', $5::jsonb))",
            пакет, значение, f"{что} вне справочника", len(каналы),
            "[" + ",".join(str(c) for c in sorted(каналы)[:100]) + "]")
        print(f"    {что} вне справочника: «{значение}», каналов {len(каналы)}, "
              f"тип у них снят в NULL — load.batch.id = {пакет}")
    return len(чужие)


def объект(строка):
    """ид_объект из справочника каналов или None.

    None значит одно из двух: колонки в файле нет вовсе (выгрузка от 09.09.2026)
    либо она пуста. В выгрузке от 15.09.2026 она заполнена у всех 11 485 каналов,
    поэтому пустое значение здесь — сигнал, а не норма: его видно в счётчике,
    который печатает load_channels.
    """
    v = (строка.get("ид_объект") or "").strip()
    return int(v) if v.isdigit() else None


async def дозаполнить_объекты(conn, rows):
    """Проставить object_id в уже залитом справочнике каналов.

    Зачем отдельный путь. На стенде справочник залит 15.09.2026, а вместе с ним
    лежат 313 млн показаний со ссылками на smvu.channel — перезаливать справочник
    ради одной новой колонки значит уронить их внешним ключом. Поэтому новый файл
    здесь не вставляет строки, а обновляет колонку у существующих.

    Обновляем только там, где сейчас NULL: если у канала объект уже стоит, а в файле
    другой, это расхождение двух выгрузок, и молча затирать его нельзя — такую строку
    считаем и называем числом.
    """
    пары = [(int(r["ид_канала_данных"]), объект(r)) for r in rows]
    пары = [(c, o) for c, o in пары if o is not None]
    if not пары:
        print("объекты каналов: колонки ид_объект в файле нет, дозаполнять нечего")
        return 0
    строка = await conn.fetchrow("""
        WITH новое(channel_id, object_id) AS (
            SELECT * FROM unnest($1::int[], $2::int[])
        ), проставлено AS (
            UPDATE smvu.channel c SET object_id = н.object_id
              FROM новое н
             WHERE c.channel_id = н.channel_id AND c.object_id IS NULL
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM проставлено) AS проставлено,
               (SELECT count(*) FROM новое н JOIN smvu.channel c USING (channel_id)
                 WHERE c.object_id IS NOT NULL AND c.object_id <> н.object_id) AS расходится
    """, [c for c, _ in пары], [o for _, o in пары])
    print(f"объекты каналов: проставлено {строка['проставлено']} из {len(пары)}")
    if строка["расходится"]:
        print(f"объекты каналов: ВНИМАНИЕ, у {строка['расходится']} каналов "
              f"объект в базе не совпадает с файлом — не трогаю, разбирать руками")
    return строка["проставлено"]


async def load_channels(conn, path):
    """Справочник каналов плюс участки, собранные из тега и названия.

    Пропускаем, если справочник уже залит, — по той же причине, что и объекты.
    Заглушки каналов из журнала сюда не считаем: их заводит flush().
    """
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if await conn.fetchval("SELECT count(*) FROM smvu.channel WHERE NOT is_stub"):
        print("каналы: уже залиты, пропускаю")
        проставлено = await дозаполнить_объекты(conn, rows)
        await дозаполнить_место(conn, rows)
        return проставлено

    keys = {}
    for r in rows:
        pair = section_key(r["тег_инженерной_системы"], r["название_датчика"])
        keys[int(r["ид_канала_данных"])] = pair
    uniq = sorted({f"{pair[0]}:{pair[1]}" for pair in keys.values() if pair})
    await conn.executemany(
        "INSERT INTO ref.object_xref (smvu_key) VALUES ($1) ON CONFLICT (smvu_key) DO NOTHING",
        [(k,) for k in uniq])
    xref = dict(await conn.fetch(
        "SELECT smvu_key, section_id FROM ref.object_xref WHERE smvu_key IS NOT NULL"))

    # Типы датчика и системы сверяем ДО вставки, и вот почему. На smvu.channel
    # висят два внешних ключа — channel_sensor_kind_fkey и channel_system_kind_fkey,
    # они смотрят в smvu.sensor_kind (19 строк) и smvu.system_kind (6 строк).
    # Замер 16.09.2026: канал с типом «Выдуманная подсистема» база отвергает
    # целиком, ForeignKeyViolationError. А строка приёмки Ф-78 требует обратного —
    # такой канал обязан ЗАГРУЗИТЬСЯ, попасть в отчёт о качестве и не прервать
    # заливку. Поэтому незнакомый тип мы снимаем в NULL, а сам факт пишем
    # в load.error: ключ остаётся на месте (опечатка не создаст седьмую систему),
    # канал остаётся в базе, отчёт называет и тип, и номера каналов.
    # Сверяем по нормализованному имени (kind_names.norm_kind): лишний пробел или
    # латинская «А» в «КД АВ» — тот же тип, а не чужой. В базу идёт имя из справочника.
    датчики = canon_map(r["sensor_kind"] for r in
                        await conn.fetch("SELECT sensor_kind FROM smvu.sensor_kind"))
    системы = canon_map(r["system_kind"] for r in
                        await conn.fetch("SELECT system_kind FROM smvu.system_kind"))
    # Вид объекта нужен location_kind: здание диспетчерской узнаём по controlHouse.
    виды = dict(await conn.fetch("SELECT object_id, kind FROM smvu.object_tree"))
    чужие = {}
    места = Counter()

    recs = []
    for r in rows:
        cid = int(r["ид_канала_данных"])
        pair = keys[cid]
        smvu_key = f"{pair[0]}:{pair[1]}" if pair else None
        тип_системы = canon(r["тип_инж_системы"], системы)
        тип_датчика = canon(r["тип_датчика"], датчики)
        if r["тип_инж_системы"] and тип_системы is None:
            чужие.setdefault(("тип инженерной системы", r["тип_инж_системы"]), []).append(cid)
        if r["тип_датчика"] and тип_датчика is None:
            чужие.setdefault(("тип датчика", r["тип_датчика"]), []).append(cid)
        tag, name = r["тег_инженерной_системы"], r["название_датчика"]
        место = location_kind(tag, name, виды.get(объект(r)))
        места[место] += 1
        # Коллектор берём из тега у каждого канала, а не только у канала с пикетом:
        # иначе 765 каналов без «ПК» в названии теряли его, хотя тег его даёт.
        recs.append((cid, тип_системы, тип_датчика, tag, name,
                     collector_of(tag), pair[1] if pair else None,
                     xref.get(smvu_key), объект(r), место, False, True))
    колонки = ["channel_id", "system_kind", "sensor_kind", "tag", "name", "collector",
               "picket", "section_id", "object_id", "location_kind", "is_stub", "is_active"]
    if not await есть_место(conn):
        # Чистая база: накат встаёт на 029 до заливки, и 031 ещё не накатана.
        # Каналы грузим без места, его проставит повторный запуск после наката.
        recs = [r[:9] + r[10:] for r in recs]
        колонки.remove("location_kind")
        print("каналы: колонки location_kind нет (миграция 031 не накатана), место "
              "проставит повторный запуск с --channels после наката")
    await conn.copy_records_to_table("channel", schema_name="smvu", records=recs,
                                     columns=колонки)
    без_участка = sum(1 for pair in keys.values() if not pair)
    с_объектом = sum(1 for r in rows if объект(r) is not None)
    print(f"каналы: {len(recs)} строк, участков {len(uniq)}, "
          f"без участка {без_участка}")
    print("каналы: место — " + ", ".join(f"{k} {места[k]}" for k in sorted(места)))
    print(f"каналы: объект дерева проставлен у {с_объектом} из {len(recs)}"
          + ("" if с_объектом else " — в файле нет колонки ид_объект (выгрузка до 15.09.2026)"))
    await записать_чужие_типы(conn, path, чужие, len(recs))
    return len(recs)


async def flush(conn, batch, records, rep, t):
    """Чанк в базу: COPY -> заглушки каналов -> INSERT в reading -> очистка."""
    await conn.copy_records_to_table(t, schema_name="load",
                                     records=records, columns=CHUNK_COLUMNS)
    stubs = await conn.execute(NEW_STUBS.format(t=t))
    status = await conn.execute(INSERT_CHUNK.format(t=t), batch)
    вставлено = int(status.split()[-1])
    rep["вставлено"] += вставлено
    # Разница может взяться только от PK: заглушки заведены строкой выше, значит
    # join с channel никого не теряет. Если счётчик начнёт расти — сначала проверить
    # это допущение, а уже потом искать дубли в файле.
    rep["дубль ид_события"] += len(records) - вставлено
    rep["заглушек каналов"] += int(stubs.split()[-1])
    await conn.execute(f"TRUNCATE load.{t}")


async def load_journal(conn, path, batch, chunk_size, t):
    """Один годовой файл журнала. Возвращает отчёт о качестве."""
    t0 = time.time()
    rep = Counter()
    buf = []
    for _, record, flag in rows_from_file(path):
        if flag:
            rep[flag] += 1
            continue
        buf.append(record)
        if len(buf) >= chunk_size:
            await flush(conn, batch, buf, rep, t)
            buf.clear()
            print(f"    {path}: {rep['вставлено']:,} строк".replace(",", " "), flush=True)
    if buf:
        await flush(conn, batch, buf, rep, t)
    dt = time.time() - t0
    скорость = rep["вставлено"] / dt if dt else 0
    print(f"{path}: вставлено {rep['вставлено']:,} за {dt / 60:.1f} мин "
          f"({скорость:,.0f} строк/с)".replace(",", " "))
    for причина, сколько in sorted(rep.items()):
        if причина != "вставлено" and сколько:
            print(f"    {причина}: {сколько:,}".replace(",", " "))
    return rep


async def drop_secondary_indexes(conn):
    for name in SECONDARY_INDEXES:
        await conn.execute(f"DROP INDEX IF EXISTS smvu.{name}")
    print(f"сняты вторичные индексы: {', '.join(SECONDARY_INDEXES)}")


async def create_secondary_indexes(conn):
    # 64 МБ по умолчанию заставляют сортировать 313 млн строк во временных файлах.
    # Гигабайт — цифра из шапки db/migrations/004_events.sql, там же целевое железо.
    await conn.execute("SET maintenance_work_mem = '1GB'")
    for name, ddl in SECONDARY_INDEXES.items():
        t0 = time.time()
        await conn.execute(ddl)
        print(f"индекс {name}: построен за {(time.time() - t0) / 60:.1f} мин", flush=True)


async def run(args):
    import asyncpg

    conn = await asyncpg.connect(args.dsn, command_timeout=None)
    try:
        # Первичная заливка: ждать подтверждения записи журнала на диск после каждой
        # транзакции здесь незачем. Упадёт база посреди заливки — мы перезальём файл
        # целиком, а не станем искать, какие последние строки не доехали.
        await conn.execute("SET synchronous_commit = off")
        await conn.execute("CREATE SCHEMA IF NOT EXISTS load")
        # Своя промежуточная таблица на поток: восемь файлов можно лить несколькими
        # процессами сразу (годы ложатся в разные месячные партиции и не мешают друг
        # другу), но общий чанк они затирали бы друг у друга TRUNCATE-ом.
        t = f"smvu_chunk_{args.slot}" if args.slot else "smvu_chunk"
        await conn.execute(CHUNK_DDL.format(t=t))
        await conn.execute(f"TRUNCATE load.{t}")

        if args.objects:
            await load_objects(conn, args.objects)
        if args.channels:
            await load_channels(conn, args.channels)
            # Геометрия — здесь, а не в миграции 046: на чистой установке миграции идут
            # до заливки, и рисовать было бы не по чему. На любом проходе --channels,
            # и на первом, и на повторе: повтор ничего не удваивает (MOS-45).
            await нарисовать_геометрию(conn)

        if args.check:
            return await check(conn)
        if args.build_indexes:
            await create_secondary_indexes(conn)
            return 0

        paths = [p for pat in args.files for p in sorted(glob.glob(pat))]
        if not paths:
            return 0

        batch = await conn.fetchval(
            "INSERT INTO load.batch (object_name, source_file, tool, planned_rows, stage) "
            "VALUES ($1, $2, 'smvu_csv', 0, 'production') RETURNING id",
            "Журнал СМВУ", ", ".join(paths))
        print(f"пакет загрузки load.batch.id = {batch}, файлов {len(paths)}, "
              f"пояс {args.tz}", flush=True)

        if not args.keep_indexes:
            await drop_secondary_indexes(conn)

        итог = Counter()
        t0 = time.time()
        for path in paths:
            итог += await load_journal(conn, path, batch, args.chunk, t)
            await conn.execute(
                "UPDATE load.batch SET loaded_rows = $2, failed_rows = $3 WHERE id = $1",
                batch, итог["вставлено"],
                sum(v for k, v in итог.items()
                    if k not in ("вставлено", "заглушек каналов")))
            # Место кончается не там, где его считали, а на седьмом файле из восьми.
            # Замер после каждого: 313 млн строк это 23 ГБ кучи и столько же индексов,
            # а на машине разработчика свободно бывает 70. Пусть нехватка видна заранее.
            занято = await conn.fetchval(
                "SELECT pg_size_pretty(pg_database_size(current_database()))")
            print(f"    база {занято}, свободно на диске "
                  f"{shutil.disk_usage('/').free / 2**30:.1f} ГБ", flush=True)

        if not args.keep_indexes:
            await create_secondary_indexes(conn)

        await conn.execute(
            "UPDATE load.batch SET finished_at = now(), planned_rows = $2 WHERE id = $1",
            batch, итог["вставлено"] + sum(
                v for k, v in итог.items() if k not in ("вставлено", "заглушек каналов")))
        # Причины отказа — в отчёт о качестве по одной строке на причину, а не по строке
        # на запись: их могут быть миллионы, и load.error раздулся бы больше самих данных.
        for причина, сколько in sorted(итог.items()):
            if причина in ("вставлено", "заглушек каналов") or not сколько:
                continue
            await conn.execute(
                "INSERT INTO load.error (batch_id, rule_code, severity, message, payload) "
                "VALUES ($1, 'ROW_REJECTED', 'warning', $2, $3::jsonb)",
                batch, причина, f'{{"строк": {сколько}}}')

        print(f"\nитого: вставлено {итог['вставлено']:,} строк за "
              f"{(time.time() - t0) / 60:.1f} мин, заглушек каналов "
              f"{итог['заглушек каналов']:,}".replace(",", " "))
        for причина, сколько in sorted(итог.items()):
            if причина not in ("вставлено", "заглушек каналов") and сколько:
                print(f"    отброшено, {причина}: {сколько:,}".replace(",", " "))
        print()
        return await check(conn)
    finally:
        await conn.close()
    return 0


# Контрольные числа выгрузки от 09.09.2026. Все получены проходом по 15,9 ГБ
# (analysis/inventory.py, docs/day-one.md) и связаны попарно: каналов в справочнике
# плюс заглушек равно каналам журнала, числовых плюс текстовых равно всем строкам.
# Пара — это проверка, которой не нужны ни данные, ни инструменты: если два числа
# перестали складываться, одно из них неверно, и пересчитывать ничего не надо.
КОНТРОЛЬНЫЕ = [
    # Повторный заголовок вычитаем: загрузчик его прочитал и отбросил, поэтому он
    # лежит в failed_rows, а опись выгрузки строкой данных его не считала. Без этой
    # поправки сверка вечно показывала бы лишнюю единицу и приучала на неё не смотреть.
    ("строк журнала",            313_546_016,
     "SELECT sum(loaded_rows + failed_rows)::bigint - coalesce("
     "(SELECT sum((payload->>'строк')::bigint) FROM load.error "
     "WHERE message = 'повторный заголовок'), 0) FROM load.batch"),
    ("каналов в журнале",             12_627,
     "SELECT count(*) FROM smvu.channel"),
    ("из них в справочнике",          11_485,
     "SELECT count(*) FROM smvu.channel WHERE NOT is_stub"),
    ("из них заглушек",                1_142,
     "SELECT count(*) FROM smvu.channel WHERE is_stub"),
    ("участков",                       3_173,
     "SELECT count(*) FROM ref.object_xref"),
    ("объектов диспетчера",               95,
     "SELECT count(*) FROM smvu.object_tree"),
]

# Проверки, у которых ожидание — не число, а утверждение о схеме.
ОБЯЗАНЫ_БЫТЬ_НУЛЁМ = [
    ("строк в партиции default", "SELECT count(*) FROM smvu.reading_default"),
    ("расхождений section_id с каналом",
     "SELECT count(*) FROM smvu.reading r JOIN smvu.channel c USING (channel_id) "
     "WHERE r.section_id IS DISTINCT FROM c.section_id"),
    ("месяцев выгрузки без единой строки",
     "SELECT count(*) FROM generate_series(date '2019-01-01', date '2026-06-01', "
     "interval '1 month') m WHERE NOT EXISTS (SELECT 1 FROM smvu.reading "
     "WHERE read_time >= m AND read_time < m + interval '1 month')"),
]


async def check(conn):
    """Сверка залитого с контрольными числами. Возвращает 1, если что-то разошлось.

    Зачем отдельная команда. «Заливка закончилась без ошибки» и «залито то, что
    в файлах» — разные утверждения: молча потерянный файл, отвалившийся join
    и съеденные дубли не роняют ни COPY, ни INSERT.
    """
    плохо = 0
    print(f"{'что':38} {'ждали':>12} {'вышло':>12}")
    for имя, ждали, sql in КОНТРОЛЬНЫЕ:
        вышло = await conn.fetchval(sql)
        сошлось = вышло == ждали
        плохо += not сошлось
        print(f"{имя:38} {ждали:>12,} {вышло:>12,} {'' if сошлось else '  ← РАЗОШЛОСЬ'}"
              .replace(",", " "))
    for имя, sql in ОБЯЗАНЫ_БЫТЬ_НУЛЁМ:
        вышло = await conn.fetchval(sql)
        плохо += вышло != 0
        print(f"{имя:38} {0:>12} {вышло:>12,}{'' if вышло == 0 else '  ← РАЗОШЛОСЬ'}"
              .replace(",", " "))

    # Не сверка, а срез: этих чисел до заливки никто не знал.
    print()
    for имя, sql in (
        ("показаний в smvu.reading", "SELECT count(*) FROM smvu.reading"),
        ("из них числовых", "SELECT count(*) FROM smvu.reading WHERE value_num IS NOT NULL"),
        ("из них тревожных", "SELECT count(*) FROM smvu.reading WHERE is_alarm"),
        ("записей «Неисправен»",
         "SELECT count(*) FROM smvu.reading WHERE value_text = 'Неисправен'"),
        ("каналов с «Неисправен»",
         "SELECT count(DISTINCT channel_id) FROM smvu.reading WHERE value_text = 'Неисправен'"),
        ("индексов на smvu.reading",
         "SELECT count(*) FROM pg_indexes WHERE schemaname = 'smvu' AND tablename = 'reading'"),
    ):
        print(f"{имя:38} {await conn.fetchval(sql):>25,}".replace(",", " "))
    print(f"\n{'размер базы':38} "
          f"{await conn.fetchval('SELECT pg_size_pretty(pg_database_size(current_database()))'):>25}")
    print("сверка: всё сошлось" if not плохо else f"сверка: расхождений {плохо}")
    return 1 if плохо else 0


def selfcheck():
    # заголовки настоящей выгрузки
    assert map_headers(["ид_события", "ид_канала_данных", "дата", "время",
                        "тревожное", "значение_датчика"]) == FIELDS
    # форма из Приложения 1 ТЗ тоже принимается
    assert map_headers(["ИД записи журнала", "ИД канала данных", "Дата записи"]) == \
        ["journal_id", "channel_id", "day_s"]
    for заголовки, кусок in ((["ид_события", "ид_канала_данных", "дата", "погода за окном"],
                              "незнакомый заголовок"),
                             (["дата", "время"], "обязательных колонок")):
        try:
            map_headers(заголовки)
        except ValueError as e:
            assert кусок in str(e), e
        else:
            raise AssertionError(f"должно было упасть: {заголовки}")

    # ид_объект: колонка появилась в выгрузке от 15.09.2026, старый файл её не знает
    assert объект({"ид_объект": "5332"}) == 5332
    assert объект({"ид_объект": " 42 "}) == 42          # пробелы по краям заказчик ставит
    assert объект({}) is None                           # выгрузка от 09.09.2026: колонки нет
    assert объект({"ид_объект": ""}) is None
    assert объект({"ид_объект": "н/д"}) is None         # не число — в NULL, а не в падение

    # оба написания булева: 2019 год пишет f/t, 2026-й false/true
    assert parse_bool("f") is False and parse_bool("t") is True
    assert parse_bool("false") is False and parse_bool("TRUE") is True
    assert parse_bool("") is None and parse_bool(None) is None and parse_bool("м. б.") is None

    # значение датчика: числа разбираются, состояния нет, и это не ошибка
    assert parse_number("28") == 28.0 and parse_number("0.01") == 0.01
    assert parse_number("25,40") == 25.40           # запятая как разделитель
    assert parse_number("Норма") is None and parse_number("Обнаружено движение") is None
    assert parse_number("01.01.1970 03:00:00") is None
    assert parse_number("") is None and parse_number(None) is None

    # время: пояс проставлен явно, иначе значение зависело бы от того, кто запустил
    t = parse_time("2026-08-01", "03:09:27")
    assert t == datetime(2026, 8, 1, 3, 9, 27, tzinfo=MSK), t
    assert t.utcoffset().total_seconds() == 3 * 3600
    assert parse_time("2026-13-01", "03:09:27") is None      # месяца 13 не бывает
    assert parse_time("01.08.2026", "03:09:27") is None      # не ISO — в отчёт, не в базу
    assert parse_time("", "03:09:27") is None

    # Разбор строк файла. Проверка обязана поймать каждую причину отказа по отдельности,
    # иначе она не отличает «файл чистый» от «загрузчик перестал замечать брак».
    header = ["ид_события", "ид_канала_данных", "дата", "время", "тревожное", "значение_датчика"]
    body = [
        ["4524243389", "120473", "2026-08-01", "03:09:27", "false", "28"],
        header,                                              # вторая выгрузка встык
        ["4524253385", "120475", "2026-08-01", "03:19:55", "true", "Неисправен"],
        ["1409185555", "213358"],                            # оборванная строка
        ["не число", "120473", "2026-08-01", "03:09:27", "f", "0.01"],
        ["4524243390", "120473", "01.08.2026", "03:09:27", "f", "0.01"],
        ["4524243391", "120473", "2026-08-01", "03:09:27", "может быть", "0.01"],
    ]
    out = list(_rows(iter(body), header, start=2))
    assert [flag for _, _, flag in out] == [
        None, "повторный заголовок", None, "короткая строка",
        "ид события или канала не число", "дата или время не разобраны",
        "флаг тревоги не разобран"], [f for _, _, f in out]
    хорошие = [rec for _, rec, flag in out if flag is None]
    assert len(хорошие) == 2
    # порядок полей записи обязан совпадать с CHUNK_COLUMNS: COPY кладёт по позиции,
    # и перепутанные местами значение и флаг база приняла бы молча
    assert хорошие[0] == (4524243389, datetime(2026, 8, 1, 3, 9, 27, tzinfo=MSK),
                          120473, False, "28", 28.0)
    assert хорошие[1][4] == "Неисправен"        # целевая переменная не теряется
    assert хорошие[1][5] is None                # и числом не притворяется
    assert len(CHUNK_COLUMNS) == len(хорошие[0])

    print("selfcheck ok")
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="файлы журнала, можно маской")
    ap.add_argument("--dsn")
    ap.add_argument("--objects", help="справочник_объектов_диспетчер.csv")
    ap.add_argument("--channels", help="справочник_каналов_датчиков.csv")
    ap.add_argument("--chunk", type=int, default=CHUNK)
    ap.add_argument("--keep-indexes", action="store_true",
                    help="не снимать вторичные индексы: для доливки поверх залитого")
    ap.add_argument("--tz", default="Europe/Moscow",
                    help="часовой пояс выгрузки; заказчик его не назвал, см. ОВ-48")
    ap.add_argument("--slot", help="номер потока: своя промежуточная таблица")
    ap.add_argument("--build-indexes", action="store_true",
                    help="построить снятые вторичные индексы и выйти")
    ap.add_argument("--check", action="store_true",
                    help="сверить залитое с контрольными числами и выйти")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if not args.dsn:
        ap.error("нужен --dsn")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
