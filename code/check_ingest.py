#!/usr/bin/env python3
"""Загрузчик выгрузки СМВУ: строки приёмки Ф-77, Ф-78, Ф-79 исполняемой проверкой.

Все три строки закрыли 15–16.09.2026 разовой заливкой (MOS-26), и до этого файла
повторить их было нечем: самопроверка `smvu_csv --selfcheck` в общий прогон не входила,
а ветки «чужой тип в отчёт» и «канал вне справочника — в заглушку» без базы не видны.

Три слоя, каждый печатает строки «Ф-NN OK …» или «Ф-NN СБОЙ …» со знаменателем:

1. Без данных и без базы — самопроверки загрузчика и разбора тега (Ф-77, Ф-79).
2. По настоящему справочнику каналов из dataset/, если он лежит рядом: все 11 485
   каналов через разбор тега, 19 типов датчика и 6 типов системы против миграции 004.
   Файла нет — строка ПРОПУСК, а не зелёная.
3. С --dsn на ПУСТУЮ базу: накат миграций и настоящая команда загрузчика
   (`python -m app.ingest.smvu_csv`) на образце — весь справочник каналов,
   два чужих типа, латиница-двойник, журнал из трёх строк с каналом вне справочника.
   На непустую базу проверка отказывается: загрузчик пишет в smvu.channel и load.*.

Запуск:  python3 code/check_ingest.py
         python3 code/check_ingest.py --dsn postgresql://…/пустая_база
Код возврата: 1, если хоть одна строка СБОЙ.
"""

import argparse
import asyncio
import csv
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest import smvu_csv, tag_to_section
from app.ingest.kind_names import canon, canon_map

# dataset/ в git не лежит; из рабочей копии git worktree её показывают через DATASET_DIR.
DATASET = Path(os.environ.get("DATASET_DIR", ROOT / "dataset"))
КАНАЛЫ = DATASET / "справочник_каналов_датчиков.csv"
ОБЪЕКТЫ = DATASET / "справочник_объектов_диспетчер.csv"
МИГРАЦИЯ_ТИПОВ = ROOT / "db" / "migrations" / "004_events.sql"

# acceptance-test.md:501 (Ф-79) называет 11 485 записей, 9 с переводом строки и участок
# у 10 720. Записей по-прежнему 11 485, а два других числа строка взяла с выгрузки
# от 09.09.2026. Пересчёт 24.09.2026 по выгрузке от 15.09.2026 (она и лежит под именем
# справочник_каналов_датчиков.csv, md5 77ef193d): переводов строки внутри названий 31
# в 27 записях, например канал 321167 «ГРО 8⏎(ПК140-162) ПК162». Участок нынешний
# разбор тега находит у 10 721 канала в обеих выгрузках.
# Поэтому переносы проверяю не константой, а равенством: физических строк файла
# ровно столько, сколько заголовок плюс записи плюс переводы строки внутри значений.
# Если разбор порвёт запись на куски, записей станет больше, и равенство не сойдётся.
ЖДЁМ_КАНАЛОВ, ЖДЁМ_С_УЧАСТКОМ = 11_485, 10_721
# Пример тега из строки Ф-79 — канал 120578 первой строки справочника: «15-11.1.131.2.», «КД АВ ПК28».
ТЕГ_ПРИМЕР = ("15-11.1.131.2.", "КД АВ ПК28", ("15", 28))

ЧУЖОЙ_ДАТЧИК, ЧУЖАЯ_СИСТЕМА = "Датчик вибрации", "Выдуманная подсистема"
КАНАЛ_ВНЕ_СПРАВОЧНИКА = 999_999_001

итог = []


def строка(код, ok, текст):
    итог.append(ok)
    print(f"{код} {'OK  ' if ok else 'СБОЙ'} {текст}")


def типы_из_миграции():
    """19 типов датчика и 6 типов системы — дословно из INSERT миграции 004."""
    sql = МИГРАЦИЯ_ТИПОВ.read_text()
    системы = sql.split("INSERT INTO smvu.system_kind", 1)[1].split(";", 1)[0]
    датчики = sql.split("INSERT INTO smvu.sensor_kind", 1)[1].split(";", 1)[0]
    return (
        re.findall(r"\('([^']+)',\s*\d+,\s*(?:true|false)\)", датчики),
        re.findall(r"\('([^']+)',\s*'", системы),
    )


def слой_без_данных():
    for код, имя, функция in (
        (
            "Ф-77",
            "загрузчик: заголовки, отказы строк, порядок полей",
            smvu_csv.selfcheck,
        ),
        ("Ф-79", "разбор тега и пикета", tag_to_section.selfcheck),
    ):
        try:
            функция()
            строка(код, True, f"самопроверка — {имя}")
        except AssertionError as e:
            строка(код, False, f"самопроверка — {имя}: {e}")


def слой_справочника():
    if not КАНАЛЫ.exists():
        print(f"Ф-78 ПРОПУСК нет {КАНАЛЫ} — справочник не сверен")
        print(f"Ф-79 ПРОПУСК нет {КАНАЛЫ} — 11 485 каналов не прогнаны")
        return
    with open(КАНАЛЫ, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    переносов = sum(r["название_датчика"].count("\n") for r in rows)
    физических = КАНАЛЫ.read_bytes().count(b"\n")
    with_section = sum(
        1
        for r in rows
        if tag_to_section.section_key(
            r["тег_инженерной_системы"], r["название_датчика"]
        )
    )
    без = [
        r
        for r in rows
        if not tag_to_section.section_key(
            r["тег_инженерной_системы"], r["название_датчика"]
        )
    ]
    причины = {
        tag_to_section.location_kind(
            r["тег_инженерной_системы"], r["название_датчика"], None
        )
        for r in без
    }
    пример = tag_to_section.section_key(*ТЕГ_ПРИМЕР[:2])
    строка(
        "Ф-79",
        len(rows) == ЖДЁМ_КАНАЛОВ and физических == 1 + len(rows) + переносов,
        f"справочник: записей {len(rows)} из {ЖДЁМ_КАНАЛОВ}; строк в файле {физических} = "
        f"заголовок 1 + записи {len(rows)} + переводы строки внутри названий {переносов} "
        f"— ни одна запись не порвана",
    )
    строка(
        "Ф-79",
        with_section == ЖДЁМ_С_УЧАСТКОМ
        and "section" not in причины
        and пример == ТЕГ_ПРИМЕР[2],
        f"участок у {with_section} из {len(rows)} (ждали {ЖДЁМ_С_УЧАСТКОМ}), у {len(без)} — "
        f"причина из {sorted(причины)}; «{ТЕГ_ПРИМЕР[0]}» + «{ТЕГ_ПРИМЕР[1]}» → {пример}",
    )

    датчики, системы = типы_из_миграции()
    в_файле_д = {r["тип_датчика"] for r in rows}
    в_файле_с = {r["тип_инж_системы"] for r in rows}
    не_нашлось = {v for v in в_файле_д if canon(v, canon_map(датчики)) is None} | {
        v for v in в_файле_с if canon(v, canon_map(системы)) is None
    }
    строка(
        "Ф-78",
        len(датчики) == 19
        and len(системы) == 6
        and len(в_файле_д) == 19
        and len(в_файле_с) == 6
        and not не_нашлось,
        f"типов датчика: в миграции {len(датчики)}, в файле {len(в_файле_д)}; типов системы: "
        f"{len(системы)} и {len(в_файле_с)}; вне справочника {len(не_нашлось)} {sorted(не_нашлось)}",
    )


def образец(каталог):
    """Весь справочник каналов + три своих; журнал из трёх строк."""
    with open(КАНАЛЫ, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        поля, rows = reader.fieldnames, list(reader)
    база = dict(rows[0])  # канал 120578 — тег из строки Ф-79
    свои = [
        {**база, "ид_канала_данных": "999000001", "тип_датчика": ЧУЖОЙ_ДАТЧИК},
        {**база, "ид_канала_данных": "999000002", "тип_инж_системы": ЧУЖАЯ_СИСТЕМА},
        {
            **база,
            "ид_канала_данных": "999000003",
            "тип_датчика": "KД AB",
        },  # латинские K, A, B
    ]
    # smvu.channel.tag уникален (channel_tag_key): свой тег на каждый, коллектор тот же.
    for i, r in enumerate(свои, 1):
        r["тег_инженерной_системы"] = f"15-99.9.9.{i}."
    каналы = Path(каталог, "каналы.csv")
    with open(каналы, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, поля)
        w.writeheader()
        w.writerows(rows + свои)
    журнал = Path(каталог, "журнал.csv")
    with open(журнал, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "ид_события",
                "ид_канала_данных",
                "дата",
                "время",
                "тревожное",
                "значение_датчика",
            ]
        )
        w.writerow(
            ["1", база["ид_канала_данных"], "2026-06-01", "10:00:00", "f", "Норма"]
        )
        w.writerow(["2", "999000001", "2026-06-01", "10:00:00", "f", "Норма"])
        w.writerow(
            [
                "3",
                str(КАНАЛ_ВНЕ_СПРАВОЧНИКА),
                "2026-06-01",
                "10:00:00",
                "t",
                "Неисправен",
            ]
        )
    return каналы, журнал, int(база["ид_канала_данных"])


def загрузчик(dsn, *аргументы):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "DATABASE_URL": dsn}
    return subprocess.run(
        [sys.executable, "-m", *аргументы],
        env=env,
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


async def слой_базы(dsn):
    import asyncpg

    if not КАНАЛЫ.exists() or not ОБЪЕКТЫ.exists():
        print(
            "Ф-78 ПРОПУСК нет справочников в dataset/ — образец для базы не из чего собрать"
        )
        return
    conn = await asyncpg.connect(dsn)
    try:
        занято = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname IN ('smvu', 'ref', 'load')"
        )
        if занято:
            строка(
                "—",
                False,
                f"база не пустая ({занято} таблиц в smvu/ref/load) — загрузчик "
                "пишет в smvu.channel и load.*, проверка гоняется только на пустой",
            )
            return
    finally:
        await conn.close()

    накат = загрузчик(dsn, "app.migrate")
    if накат.returncode:
        строка("—", False, f"накат миграций упал: {накат.stderr.strip()[-300:]}")
        return
    with tempfile.TemporaryDirectory() as каталог:
        каналы, журнал, канал_тега = образец(каталог)
        справочники = загрузчик(
            dsn,
            "app.ingest.smvu_csv",
            "--dsn",
            dsn,
            "--objects",
            str(ОБЪЕКТЫ),
            "--channels",
            str(каналы),
        )
        if справочники.returncode:
            строка(
                "Ф-78",
                False,
                f"загрузка справочников упала: {справочники.stderr.strip()[-300:]}",
            )
            return
        # Код возврата второго шага не смотрим: в конце загрузчик сверяет базу
        # с контрольными числами всей выгрузки (313 546 016 строк) и на образце их не сойдётся.
        # Что строки легли, проверяем запросами ниже, а не кодом.
        загрузчик(dsn, "app.ingest.smvu_csv", "--dsn", dsn, str(журнал))

    with open(КАНАЛЫ, encoding="utf-8", newline="") as f:
        переносов_в_файле = sum(
            r["название_датчика"].count("\n") for r in csv.DictReader(f)
        )
    conn = await asyncpg.connect(dsn)
    try:
        q = conn.fetchval
        всего = await q("SELECT count(*) FROM smvu.channel WHERE NOT is_stub")
        чужой_д = await conn.fetchrow(
            "SELECT sensor_kind, system_kind FROM smvu.channel WHERE channel_id = 999000001"
        )
        чужая_с = await conn.fetchrow(
            "SELECT sensor_kind, system_kind FROM smvu.channel WHERE channel_id = 999000002"
        )
        двойник = await q(
            "SELECT sensor_kind FROM smvu.channel WHERE channel_id = 999000003"
        )
        отчёт = {
            r["source_key"]: (r["message"], r["n"])
            for r in await conn.fetch(
                "SELECT source_key, message, (payload->>'каналов')::int AS n FROM load.error "
                "WHERE rule_code = 'FK_MISSING'"
            )
        }
        переносов_в_базе = await q(
            "SELECT sum(length(name) - length(replace(name, E'\\n', ''))) "
            "FROM smvu.channel WHERE NOT is_stub"
        )
        строка(
            "Ф-79",
            всего == ЖДЁМ_КАНАЛОВ + 3 and переносов_в_базе == переносов_в_файле,
            f"загрузчик залил каналов {всего} = {ЖДЁМ_КАНАЛОВ} + 3 своих; переводов строки внутри "
            f"названий в базе {переносов_в_базе}, в файле {переносов_в_файле}",
        )
        строка(
            "Ф-78",
            чужой_д is not None
            and чужой_д is not None
            and чужой_д["sensor_kind"] is None
            and чужая_с is not None
            and чужая_с["system_kind"] is None
            and отчёт.get(ЧУЖОЙ_ДАТЧИК) == ("тип датчика вне справочника", 1)
            and отчёт.get(ЧУЖАЯ_СИСТЕМА)
            == ("тип инженерной системы вне справочника", 1),
            f"чужой тип датчика → {чужой_д and чужой_д['sensor_kind']}, "
            f"чужой тип системы → {чужая_с and чужая_с['system_kind']}; в load.error {отчёт}",
        )
        строка(
            "Ф-78",
            двойник == "КД АВ",
            f"«KД AB» латиницей записан как {двойник!r} — тип из справочника, а не чужой",
        )

        участок = await q(
            "SELECT x.smvu_key FROM smvu.channel c JOIN ref.object_xref x USING (section_id) "
            "WHERE c.channel_id = $1",
            канал_тега,
        )
        заглушка = await conn.fetchrow(
            "SELECT is_stub, is_active FROM smvu.channel WHERE channel_id = $1",
            КАНАЛ_ВНЕ_СПРАВОЧНИКА,
        )
        показаний = await q("SELECT count(*) FROM smvu.reading")
        показание = await conn.fetchrow(
            "SELECT section_id, value_text FROM smvu.reading WHERE channel_id = $1",
            КАНАЛ_ВНЕ_СПРАВОЧНИКА,
        )
        # Отбор каналов в расчёт — дословно из backend/app/worker/run_v3.py:59.
        в_расчёт = await q(
            "SELECT count(*) FROM smvu.channel c WHERE c.is_active AND c.section_id IS NOT NULL "
            "AND c.channel_id = $1",
            КАНАЛ_ВНЕ_СПРАВОЧНИКА,
        )
        строка(
            "Ф-79",
            участок == f"{ТЕГ_ПРИМЕР[2][0]}:{ТЕГ_ПРИМЕР[2][1]}",
            f"канал {канал_тега} с тегом «{ТЕГ_ПРИМЕР[0]}» лёг на участок {участок!r}",
        )
        строка(
            "Ф-79",
            заглушка is not None
            and заглушка["is_stub"]
            and not заглушка["is_active"]
            and показаний == 3
            and показание is not None
            and показание["section_id"] is None
            and показание["value_text"] == "Неисправен"
            and в_расчёт == 0,
            f"канал вне справочника {КАНАЛ_ВНЕ_СПРАВОЧНИКА}: заглушка {заглушка and dict(заглушка)}, "
            f"показаний легло {показаний} из 3, его показание {показание and dict(показание)}, "
            f"в отбор расчёта попал {в_расчёт} раз",
        )
    finally:
        await conn.close()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dsn", help="строка подключения к ПУСТОЙ базе; без неё слой базы — ПРОПУСК"
    )
    args = ap.parse_args(argv)
    слой_без_данных()
    слой_справочника()
    if args.dsn:
        asyncio.run(слой_базы(args.dsn))
    else:
        print("Ф-78 ПРОПУСК слой базы — задайте --dsn пустой базы")
    print(f"итого: OK {sum(итог)}, СБОЙ {len(итог) - sum(итог)}")
    return 1 if not all(итог) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
