#!/usr/bin/env python3
"""Парсер графика ТО/ТР систем АКМ и ДУ на 2026 г. (РЭК).

Файл: График_ТО_АКМ_и_ДУ_на_2026г_РЭК_3_на_А4.xlsx (прислан заказчиком 25.09.2026).

В окружении нет pandas/openpyxl, поэтому XLSX разбирается вручную через
zipfile + xml.etree: sharedStrings.xml, inlineStr, объединённые ячейки,
даты Excel (эпоха 1899-12-30) по числовому формату стиля — в этом файле
дат нет вообще, держим на будущее.

Фактическая схема листа «2025 год» (единственный лист; имя вкладки не
переименовали после копирования файла 2025 г., в заголовке — «на 2026г»):
  B  — № п.п. объекта (1..24), заполнен только на строках-заголовках объектов;
  C  — имя объекта на строке-заголовке («Объект» или «к-р Ясенево»),
       у оборудования пусто (в шапке подписано «Вид оборудования»);
  D  — вид/наименование оборудования (в шапке подписано «Марка»);
  E  — количество, F — единица (шт./м.);
  G..R — месяцы янв..дек 2026, значение = вид работ: ТО, ТР или ТО+ТР;
  S  — «Примечания» (в данных не заполнена ни разу).

Выход (analysis/to_ppr/):
  to_schedule.csv — нормализованный «длинный» список: одна строка = одна
                    запланированная работа (оборудование × месяц); строки без
                    отметок идут с пустым месяцем, чтобы не потерялся ни один
                    объект исходника;
  to_grid.tsv     — полный дамп сетки листа.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "График_ТО_АКМ_и_ДУ_на_2026г_РЭК_3_на_А4.xlsx"
OUT_DIR = Path(__file__).resolve().parent

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EXCEL_EPOCH = dt.date(1899, 12, 30)

MONTHS = ["янв", "февр", "март", "апр", "май", "июн",
          "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTH_COL = {7 + i: MONTHS[i] for i in range(12)}   # G..R -> янв..дек
WORKS = {"ТО", "ТР", "ТО+ТР"}


def col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n  # 1-based


def idx_to_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def split_ref(ref: str) -> tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref)
    return col_to_idx(m.group(1)), int(m.group(2))


def load_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        xml = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    return ["".join(t.text or "" for t in si.iter(f"{NS}t"))
            for si in root.findall(f"{NS}si")]


def load_date_styles(z: zipfile.ZipFile) -> set[int]:
    root = ET.fromstring(z.read("xl/styles.xml"))
    custom = {int(nf.get("numFmtId")): nf.get("formatCode", "")
              for nf in root.iter(f"{NS}numFmt")}
    date_ids = {14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 28, 29, 30, 31,
                32, 33, 34, 35, 36, 45, 46, 47, 50, 51, 52, 53, 54, 55,
                56, 57, 58}
    styles: set[int] = set()
    cell_xfs = root.find(f"{NS}cellXfs")
    if cell_xfs is None:
        return styles
    for i, xf in enumerate(cell_xfs.findall(f"{NS}xf")):
        fid = int(xf.get("numFmtId", "0"))
        if fid in date_ids or re.search(r"[dmyhs]", custom.get(fid, "").lower()):
            styles.add(i)
    return styles


def excel_serial_to_str(v: float) -> str:
    d = EXCEL_EPOCH + dt.timedelta(days=int(v))
    frac = v - int(v)
    if abs(frac) > 1e-9:
        secs = round(frac * 86400)
        return f"{d.isoformat()}T{secs // 3600:02d}:{secs % 3600 // 60:02d}"
    return d.isoformat()


def load_sheet(z: zipfile.ZipFile, sheet_path: str, sst: list[str],
               date_styles: set[int]):
    root = ET.fromstring(z.read(sheet_path))
    cells: dict[tuple[int, int], str] = {}
    merges: list[tuple[int, int, int, int]] = []
    hidden_rows: set[int] = set()
    for mc in root.iter(f"{NS}mergeCell"):
        a, b = mc.get("ref").split(":")
        c1, r1 = split_ref(a)
        c2, r2 = split_ref(b)
        merges.append((r1, c1, r2, c2))
    for row in root.iter(f"{NS}row"):
        r = int(row.get("r"))
        if row.get("hidden") == "1":
            hidden_rows.add(r)
        for c in row.findall(f"{NS}c"):
            col, _ = split_ref(c.get("r"))
            t = c.get("t", "n")
            s = int(c.get("s", "0"))
            v = c.find(f"{NS}v")
            if t == "inlineStr":
                is_el = c.find(f"{NS}is")
                val = "".join(x.text or "" for x in is_el.iter(f"{NS}t")) \
                    if is_el is not None else ""
            elif t == "s" and v is not None:
                val = sst[int(v.text)]
            elif t == "str" and v is not None:
                val = v.text or ""
            elif t == "b" and v is not None:
                val = "TRUE" if v.text == "1" else "FALSE"
            elif v is not None and v.text is not None:
                raw = v.text
                if s in date_styles:
                    try:
                        val = excel_serial_to_str(float(raw))
                    except ValueError:
                        val = raw
                else:
                    val = raw[:-2] if raw.endswith(".0") else raw
            else:
                val = ""
            val = val.strip()
            if val:
                cells[(r, col)] = val
    for r1, c1, r2, c2 in merges:  # тянем якорь в объединённый диапазон
        anchor = cells.get((r1, c1), "")
        if anchor:
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    cells.setdefault((r, c), anchor)
    return cells, merges, hidden_rows


def periodicity(month_nums: list[int]) -> str:
    """Описывает периодичность по списку отмеченных месяцев (1..12)."""
    if not month_nums:
        return ""
    ms = sorted(month_nums)
    if len(ms) == 1:
        return "1 раз/год"
    gaps = {b - a for a, b in zip(ms, ms[1:])}
    if len(gaps) == 1:
        step = gaps.pop()
        return f"{len(ms)} раз/год, шаг {step} мес"
    return f"{len(ms)} раз/год, шаг неравномерный ({min(gaps)}–{max(gaps)} мес)"


def main() -> int:
    z = zipfile.ZipFile(XLSX)
    sst = load_shared_strings(z)
    date_styles = load_date_styles(z)
    cells, merges, hidden_rows = load_sheet(
        z, "xl/worksheets/sheet1.xml", sst, date_styles)

    max_row = max(r for r, _ in cells)
    max_col = max(c for _, c in cells)

    # ---- 1. полный дамп сетки -----------------------------------------
    with (OUT_DIR / "to_grid.tsv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["row"] + [idx_to_col(c) for c in range(1, max_col + 1)])
        for r in range(1, max_row + 1):
            row = [str(r)] + [cells.get((r, c), "") for c in range(1, max_col + 1)]
            if any(row[1:]):
                w.writerow(row)

    # ---- 2. нормализация ----------------------------------------------
    obj_num, obj_name = "", ""
    out: list[dict[str, str]] = []
    equip_rows = 0
    obj_rows = 0
    marks = Counter()
    qty_by_type: Counter = Counter()
    seen_objects: list[tuple[str, str]] = []

    for r in range(1, max_row + 1):
        b = cells.get((r, 2), "").strip()
        c = cells.get((r, 3), "").strip()
        d = cells.get((r, 4), "").strip()
        e = cells.get((r, 5), "").strip()
        f = cells.get((r, 6), "").strip()
        note = cells.get((r, 19), "").strip()
        hidden = "1" if r in hidden_rows else "0"

        is_obj_header = (c in ("Объект", "к-р Ясенево") or
                         (c not in ("", "Вид оборудования") and not d and not e))
        if is_obj_header and b and b != "№ п.п.":
            if (b, c) == (obj_num, obj_name):
                continue  # вторая половина объединённого заголовка (стр. 44–45)
            obj_num, obj_name = b, c
            seen_objects.append((obj_num, obj_name))
            obj_rows += 1
            if not d:  # чистый заголовок объекта — работ на нём нет
                continue
            # редкий случай: заголовок объекта и оборудование на одной строке

        if not d or d in ("Марка", "Вид оборудования"):
            continue
        equip_rows += 1
        month_nums = []
        for col, mon in MONTH_COL.items():
            v = cells.get((r, col), "").strip()
            if v in WORKS:
                month_nums.append(col - 6)
                marks[v] += 1
        try:
            qty_by_type[d] += float(e.replace(",", ".")) if e else 0.0
        except ValueError:
            pass

        base = {
            "строка_xlsx": r,
            "строка_скрыта": hidden,
            "объект_номер": obj_num,
            "объект_имя": obj_name,
            "вид_оборудования": d,
            "количество": e,
            "единица": f,
            "периодичность": periodicity(month_nums),
            "примечание": note,
        }
        if not month_nums:
            out.append({**base, "вид_работ": "", "месяц_план": ""})
        else:
            for mn in month_nums:
                v = cells.get((r, 6 + mn), "").strip()
                out.append({**base, "вид_работ": v,
                            "месяц_план": f"2026-{mn:02d}"})

    fields = ["строка_xlsx", "строка_скрыта", "объект_номер", "объект_имя",
              "вид_оборудования", "количество", "единица", "вид_работ",
              "месяц_план", "периодичность", "примечание"]
    with (OUT_DIR / "to_schedule.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)

    # ---- 3. сводка в stdout -------------------------------------------
    print(f"строк с данными на листе: {max_row}, колонок: {max_col}, "
          f"объединённых диапазонов: {len(merges)}, скрытых строк: {sorted(hidden_rows)}")
    print(f"строк-заголовков объектов: {obj_rows}, уникальных объектов: {len(seen_objects)}")
    print(f"строк оборудования: {equip_rows}, записей в to_schedule.csv: {len(out)}")
    print("отметки работ:", dict(marks), "всего:", sum(marks.values()))
    print("объекты:", seen_objects)
    print("количество по видам оборудования:")
    for k, v in sorted(qty_by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v:g}")


if __name__ == "__main__":
    sys.exit(main())
