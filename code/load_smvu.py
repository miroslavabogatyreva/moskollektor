#!/usr/bin/env python3
"""Заливка выгрузок СМВУ в PostgreSQL: csv и xlsx.

Что читаем. Выгрузка от 09.09.2026 пришла в csv с шестью колонками:

    ид_события, ид_канала_данных, дата, время, тревожное, значение_датчика
    4524243389, 120473, "2026-08-01", "03:09:27", false, "28"

Прежний загрузчик ждал колонки «ID датчика», «Тип события», «Адрес» и «Результат
проверки». Ни одной из них в файлах заказчика нет, и он падал на первом же заголовке.
Правильно падал: молча загруженные сдвинутые колонки мы бы искали неделю.

Рецепт в трёх шагах, без промежуточных файлов:
    чтение потоком -> normalize() чистит ячейку -> COPY в staging, весь в text.

Почему staging весь в text. Разбор типов делает SQL после заливки, а не Python:
битая метка времени должна попасть в отчёт о качестве с номером строки файла,
а не уронить COPY на 20-миллионной строке после двадцати минут работы.

Почему COPY, а не пакетный INSERT: замер Tiger Data — 232-316 тыс. строк/с против
31-38 тыс. На файле за 2019 год (1,18 ГБ, 20 993 328 строк) это 70-90 секунд.

Почему calamine для xlsx, если его пока не прислали. Замер Haki Benita на 500 тыс.
строк: calamine 3,58 с против pandas 32,98 и openpyxl 35,62. Плюс openpyxl держит книгу
в памяти примерно 50-кратным объёмом файла, и 50 МБ xlsx съедают 2,5 ГБ ОЗУ.

Четыре ловушки настоящей выгрузки, из-за которых загрузчик и переписан:

1. В ext-journal-2025.csv ЗАГОЛОВОК СТОИТ ДВАЖДЫ: на первой строке и на 26 140 585-й.
   Две выгрузки склеены встык. `COPY ... WITH (HEADER true)` пропускает ровно одну
   строку, второй заголовок приедет как данные, «ид_события» не разберётся в число,
   и Postgres откатит весь файл после двадцати пяти минут работы. Поэтому повторный
   заголовок мы выбрасываем при чтении и считаем отдельной строкой отчёта.
2. Булево пишется по-разному: в файлах 2019 года это `f` и `t`, в примере за август
   2026 — `false` и `true`. Понимаем оба написания.
3. ФАЙЛ ГОДА НЕ ОТСОРТИРОВАН ПО ВРЕМЕНИ И НЕ НАЧИНАЕТСЯ С ЯНВАРЯ. Три файла
   из восьми склеены из нескольких отсортированных кусков, переставленных местами.
   Отметки по позиции в файле:
     2019  начало 02-11, середина 08-10, КОНЕЦ 02-09   (январь лежит в хвосте)
     2021  начало 06-06, четверть 04-21, конец 12-28   (кусков минимум три)
     2022  начало 05-11, позиция 28 млн 01-13          (май-декабрь, потом январь-май)
     2020, 2023, 2024, 2025, 2026 — по возрастанию
   Внутри кусков порядок есть: шагов назад во времени в файле 2022 года всего 0,02 %.
   Практическое следствие, на котором легко обжечься: «первые N строк файла» это
   НЕ «самые ранние N строк». Любая выборка по началу файла даёт случайный месяц,
   а не начало года. Проверяйте даты в своей выборке, прежде чем подписывать результат.
   Для COPY это безразлично — партиции маршрутизируются по значению. А вот BRIN-индекс
   по времени опирается на совпадение физического порядка с хронологическим: два-три
   куска он переживает, потому что внутри них сортировка есть, но проверить это надо
   на стенде через pg_stats.correlation, а не считать доказанным.

4. Дата и время лежат ДВУМЯ колонками, часовой пояс нигде не назван. Склеиваем их,
   пояс проставляем явно из --tz (по умолчанию Europe/Moscow) и записываем это решение
   в отчёт. Догадка обоснована: среди значений встречается «01.01.1970 03:00:00» —
   нулевая эпоха, сдвинутая на три часа. Но это догадка, и вопрос заказчику открыт.

Запуск:
    python3 code/load_smvu.py --dsn "postgresql://..." dataset/ext-journal-*.csv
    python3 code/load_smvu.py --selfcheck
"""

import argparse
import csv
import glob
import sys
import time
from datetime import date, datetime

STAGING_DDL = """
CREATE UNLOGGED TABLE IF NOT EXISTS load.smvu_raw (
    batch_id     bigint,
    file_row     integer,
    journal_id   text,
    channel_id   text,
    day_s        text,
    time_s       text,
    is_alarm_s   text,
    value_s      text
);
"""

# Ключ — нормализованный заголовок из файла, значение — колонка staging.
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

COLUMNS = ["journal_id", "channel_id", "day_s", "time_s", "is_alarm_s", "value_s"]

# Как заказчик пишет истину и ложь. Файлы 2019 года — f/t, пример 2026 года — false/true.
TRUE_WORDS = {"t", "true", "1", "да", "истина"}
FALSE_WORDS = {"f", "false", "0", "нет", "ложь"}


def norm_header(h):
    return str(h).strip().lower().replace("ё", "е").replace("\n", " ")


def map_headers(row):
    """Список колонок staging по заголовку файла. None — колонку игнорируем."""
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
    («Норма», «Обнаружено движение») и «01.01.1970 03:00:00» — нулевую эпоху, которая
    означает «значения нет». Замер на первых 2 млн строк файла за 2019 год: числами
    разбираются 47,1 %, остальные 52,9 % числами не являются и обязаны лечь
    в value_text, а не потеряться.
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


def normalize(cell):
    """Одно значение ячейки -> строка для COPY. Пусто -> \\N."""
    if cell is None or cell == "":
        return r"\N"
    if isinstance(cell, (datetime, date)):
        return cell.isoformat()
    s = str(cell)
    # COPY в текстовом формате ест табы и переводы строк как разделители — экранируем.
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


def rows_from_file(path):
    """Генератор (номер строки в файле, значения в порядке COLUMNS, пометка).

    Пометка — None для обычной строки либо причина, по которой строку надо
    посчитать отдельно: "повторный заголовок", "короткая строка".
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
        # Ловушка 1: две выгрузки склеены встык, заголовок повторяется внутри файла.
        if [norm_header(c) for c in raw] == header_norm:
            yield n, None, "повторный заголовок"
            continue
        if len(raw) < width:
            yield n, None, "короткая строка"
            continue
        yield n, [normalize(raw[idx[c]]) if c in idx else r"\N" for c in COLUMNS], None


def copy_file(conn, path, batch_id):
    """Заливает один файл в staging, возвращает отчёт о качестве."""
    t0 = time.time()
    rep = {"файл": path, "принято": 0, "повторный заголовок": 0, "короткая строка": 0}
    with conn.cursor() as cur:
        with cur.copy("COPY load.smvu_raw (batch_id, file_row, " +
                      ", ".join(COLUMNS) + ") FROM STDIN") as cp:
            for file_row, values, flag in rows_from_file(path):
                if flag:
                    rep[flag] += 1
                    continue
                cp.write("\t".join([str(batch_id), str(file_row)] + values) + "\n")
                rep["принято"] += 1
    dt = time.time() - t0
    rate = rep["принято"] / dt if dt else 0
    print(f"{path}: принято {rep['принято']} строк за {dt:.1f} с ({rate:,.0f} строк/с)"
          .replace(",", " "))
    for key in ("повторный заголовок", "короткая строка"):
        if rep[key]:
            print(f"    отброшено, {key}: {rep[key]}")
    return rep


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dsn")
    ap.add_argument("--batch-id", type=int, default=1)
    ap.add_argument("--tz", default="Europe/Moscow",
                    help="часовой пояс выгрузки; заказчик его не назвал, см. ОВ-48")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if not args.dsn or not args.files:
        ap.error("нужны --dsn и хотя бы один файл")

    import psycopg

    paths = [p for pat in args.files for p in sorted(glob.glob(pat))]
    total = 0
    with psycopg.connect(args.dsn) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS load")
        conn.execute(STAGING_DDL)
        # Индексов на staging нет и не должно быть: они утроят время COPY.
        for i, path in enumerate(paths):
            total += copy_file(conn, path, args.batch_id + i)["принято"]
        conn.commit()
    print(f"итого {total} строк в load.smvu_raw, пояс {args.tz}")
    print("дальше: разбор в smvu.reading, заглушки каналов вне справочника, отчёт о качестве")
    return 0


def selfcheck():
    # заголовки настоящей выгрузки
    assert map_headers(["ид_события", "ид_канала_данных", "дата", "время",
                        "тревожное", "значение_датчика"]) == COLUMNS
    # форма из Приложения 1 ТЗ тоже принимается
    assert map_headers(["ИД записи журнала", "ИД канала данных", "Дата записи"]) == \
        ["journal_id", "channel_id", "day_s"]
    try:
        map_headers(["ид_события", "ид_канала_данных", "дата", "погода за окном"])
    except ValueError as e:
        assert "незнакомый заголовок" in str(e)
    else:
        raise AssertionError("незнакомый заголовок должен ронять разбор")
    try:
        map_headers(["дата", "время"])
    except ValueError as e:
        assert "обязательных колонок" in str(e)
    else:
        raise AssertionError("отсутствие обязательных колонок должно ронять разбор")

    # оба написания булева: 2019 год пишет f/t, 2026-й false/true
    assert parse_bool("f") is False and parse_bool("t") is True
    assert parse_bool("false") is False and parse_bool("true") is True
    assert parse_bool("TRUE") is True
    assert parse_bool("") is None and parse_bool(None) is None
    assert parse_bool("может быть") is None

    # значение датчика: числа разбираются, состояния нет, и это не ошибка
    assert parse_number("28") == 28.0
    assert parse_number("0.01") == 0.01
    assert parse_number("25,40") == 25.40           # запятая как разделитель
    assert parse_number("Норма") is None
    assert parse_number("Обнаружено движение") is None
    assert parse_number("01.01.1970 03:00:00") is None
    assert parse_number("") is None and parse_number(None) is None

    assert normalize(None) == r"\N"
    assert normalize("") == r"\N"
    assert normalize(datetime(2024, 3, 1, 12, 30)) == "2024-03-01T12:30:00"
    assert normalize("а\tб\nв") == "а\\tб\\nв"

    # повторный заголовок внутри файла выбрасывается, а не уходит в данные:
    # ровно этим ext-journal-2025.csv уронил бы COPY после 25 минут работы
    header = ["ид_события", "ид_канала_данных", "дата", "время", "тревожное", "значение_датчика"]
    body = [
        ["4524243389", "120473", "2026-08-01", "03:09:27", "false", "28"],
        header,                                        # вторая выгрузка встык
        ["4524253385", "120475", "2026-08-01", "03:19:55", "true", "Неисправен"],
        ["1409185555", "213358"],                      # оборванная строка
    ]
    out = list(_rows(iter(body), header, start=2))
    flags = [flag for _, _, flag in out]
    assert flags == [None, "повторный заголовок", None, "короткая строка"], flags
    good = [vals for _, vals, flag in out if flag is None]
    assert len(good) == 2
    assert good[0] == ["4524243389", "120473", "2026-08-01", "03:09:27", "false", "28"]
    assert good[1][5] == "Неисправен"                  # целевая переменная не теряется

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
