#!/usr/bin/env python3
"""Заливка исторических выгрузок СМВУ (.xlsx) в PostgreSQL.

Рецепт в трёх шагах, без промежуточных файлов:
    python_calamine читает лист потоком -> normalize() чистит строку -> COPY в staging.

Почему так, а не pandas.read_excel:
  * замер Haki Benita (25 МБ, 500 000 строк): calamine 3,58 с против pandas 32,98 с
    и openpyxl 35,62 с — разница в 9–10 раз;
  * openpyxl держит книгу в памяти примерно 50-кратным объёмом файла: 50 МБ xlsx
    съедают ~2,5 ГБ ОЗУ. На сервере с 16 ГБ это уже больно, и ради этого весь рецепт.
  * COPY даёт 5–10-кратную пропускную способность против пакетного INSERT
    (замер Tiger Data: 232–316 тыс. строк/с против 31–38 тыс.).

Staging — UNLOGGED и весь в text. Проверки типов делает SQL после заливки, а не Python:
битая дата «32.13.2024» должна попасть в журнал ошибок с номером строки файла,
а не уронить загрузку на 400-тысячной строке.

Запуск:
    python load_smvu_xlsx.py --dsn "postgresql://..." /путь/выгрузки/*.xlsx
    python load_smvu_xlsx.py --selfcheck
"""

import argparse
import glob
import sys
import time
from datetime import datetime, date

STAGING_DDL = """
CREATE UNLOGGED TABLE IF NOT EXISTS load.smvu_raw (
    batch_id      bigint,
    file_row      integer,
    external_id   text,
    event_time_s  text,
    type_code     text,
    address_s     text,
    value_s       text,
    check_s       text
);
"""

# Заголовки в выгрузках СМВУ гуляют от файла к файлу. Ключ — нормализованный заголовок,
# значение — наша колонка. Незнакомый заголовок -> падаем с явной ошибкой, а не молча
# грузим сдвинутые колонки.
HEADER_MAP = {
    "id датчика": "external_id", "идентификатор датчика": "external_id",
    "датчик": "external_id", "номер датчика": "external_id",
    "дата и время": "event_time_s", "время события": "event_time_s",
    "дата": "event_time_s", "timestamp": "event_time_s",
    "тип": "type_code", "тип события": "type_code", "тип сигнала": "type_code",
    "адрес": "address_s", "адрес объекта": "address_s",
    "значение": "value_s", "показание": "value_s",
    "результат проверки": "check_s", "результат": "check_s", "итог выезда": "check_s",
}

COLUMNS = ["external_id", "event_time_s", "type_code", "address_s", "value_s", "check_s"]


def norm_header(h):
    return str(h).strip().lower().replace("ё", "е").replace("\n", " ")


def map_headers(row):
    """Возвращает список наших имён колонок по заголовку файла."""
    out = []
    for h in row:
        key = norm_header(h)
        if key in HEADER_MAP:
            out.append(HEADER_MAP[key])
        elif key == "" or key.startswith("unnamed"):
            out.append(None)
        else:
            raise ValueError(f"незнакомый заголовок колонки: {h!r}")
    missing = {"external_id", "event_time_s", "type_code"} - set(out)
    if missing:
        raise ValueError(f"в файле нет обязательных колонок: {sorted(missing)}")
    return out


def normalize(cell):
    """Одно значение ячейки -> строка для COPY. None -> \\N."""
    if cell is None or cell == "":
        return r"\N"
    if isinstance(cell, (datetime, date)):
        return cell.isoformat()
    s = str(cell)
    # COPY в текстовом формате ест табы и переводы строк как разделители — экранируем.
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


def rows_from_xlsx(path):
    """Ленивый генератор строк: (номер строки в файле, [значения в порядке COLUMNS])."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(path)
    sheet = wb.get_sheet_by_index(0)
    data = sheet.to_python(skip_empty_area=False)
    if not data:
        return
    order = map_headers(data[0])
    idx = {name: i for i, name in enumerate(order) if name}
    for n, raw in enumerate(data[1:], start=2):
        yield n, [normalize(raw[idx[c]]) if c in idx and idx[c] < len(raw) else r"\N"
                  for c in COLUMNS]


def copy_file(conn, path, batch_id):
    t0 = time.time()
    n = 0
    with conn.cursor() as cur:
        with cur.copy("COPY load.smvu_raw (batch_id, file_row, " +
                      ", ".join(COLUMNS) + ") FROM STDIN") as cp:
            for file_row, values in rows_from_xlsx(path):
                cp.write("\t".join([str(batch_id), str(file_row)] + values) + "\n")
                n += 1
    dt = time.time() - t0
    print(f"{path}: {n} строк за {dt:.1f} с ({n / dt:,.0f} строк/с)".replace(",", " "))
    return n


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dsn")
    ap.add_argument("--batch-id", type=int, default=1)
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
            total += copy_file(conn, path, args.batch_id + i)
        conn.commit()
    print(f"итого {total} строк в load.smvu_raw; дальше — SQL-разбор и вставка в smvu.event")
    return 0


def selfcheck():
    assert normalize(None) == r"\N"
    assert normalize("") == r"\N"
    assert normalize(datetime(2024, 3, 1, 12, 30)) == "2024-03-01T12:30:00"
    assert normalize("а\tб\nв") == "а\\tб\\nв", normalize("а\tб\nв")
    assert normalize(0.2) == "0.2"
    assert map_headers(["ID датчика", "Дата и время", "Тип", "Адрес"]) == \
        ["external_id", "event_time_s", "type_code", "address_s"]
    assert map_headers(["Датчик", "Время события", "Тип сигнала", "Unnamed: 3"]) == \
        ["external_id", "event_time_s", "type_code", None]
    try:
        map_headers(["Датчик", "Время события", "Погода за окном"])
    except ValueError as e:
        assert "незнакомый заголовок" in str(e)
    else:
        raise AssertionError("незнакомый заголовок должен ронять разбор")
    try:
        map_headers(["Адрес", "Значение"])
    except ValueError as e:
        assert "обязательных колонок" in str(e)
    else:
        raise AssertionError("отсутствие обязательных колонок должно ронять разбор")
    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
