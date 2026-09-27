#!/usr/bin/env python3
"""Приёмка 0.3 «Модуль автоматического формирования заявок»: строки М-09…М-13.

Скрипт спрашивает базу и печатает пять строк вида «М-NN OK …» или «М-NN СБОЙ …».
Доказательство — числа, а не слово «пройдено»: каждая строка называет и долю,
и знаменатель, от которого доля посчитана. «100 % заполнены» на нуле заявок —
это зелень на пустоте, поэтому ноль заявок здесь СБОЙ, а не молчаливый ноль.

Код возврата: 1, если хоть одна строка красная; 0 только когда все пять зелёные.

ЧТО ЭТОТ СКРИПТ НЕ ДОКАЗЫВАЕТ. Он спрашивает базу и только базу. Строка М-12
требует ещё и перехода заявка → прогноз → заявка через API и на экране, а этого
здесь нет вовсе — подробности в docstring функции check_m12. Поэтому «М-12 OK»
отсюда читается как «связь лежит в данных», а не как «строка приёмки закрыта».
Так же и с М-11: скрипт видит заполненные поля, но не видит, как они показаны
в карточке. Зелёный прогон — необходимое условие, не достаточное.

Проверка по умолчанию отказывает. Нет DATABASE_URL, не поднялась база, нет
таблицы, ноль строк — всё это СБОЙ. На 17.09.2026 блок Q6 (модуль заявок) ещё
не написан, автозаявок в базе нет, и скрипт обязан быть красным целиком.

Подключение то же, что у бэкенда (backend/app/db.py): asyncpg и переменная
DATABASE_URL. Своего способа не заводим, иначе стенд и приёмка смотрят в разные
базы и расходятся молча.

М-10 и М-13 спрашивают ещё и выдачу модели — score.json с открытыми предупреждениями
и их историей (контракт score.v3). В базе предупреждений нет: worker читает файл
и складывает флаг в вероятность коллектора. Путь к файлу — SCORE_JSON; check-all.sh
забирает его со стенда сам, если задан STAND_SSH. Без файла обе строки — СБОЙ.

Запуск:  DATABASE_URL=postgresql://... SCORE_JSON=score.json python3 code/check_orders.py
Самопроверка без базы (М-10, М-13):  python3 code/check_orders.py --demo
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

try:
    import asyncpg
except ImportError:  # пакет живёт в образе бэкенда, локально его может не быть
    asyncpg = None

# Участок -> коллектор берём из продукта, а не пишем второй раз: этим же запросом
# worker раскладывает вероятность коллектора по участкам. Своя копия разошлась бы
# с продуктом молча — ровно та беда, которую проверка должна ловить, а не носить.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.worker.run_v3 import УЧАСТКИ_КОЛЛЕКТОРА  # noqa: E402

# Медиану упреждения для М-13 считает та же методика, что М-18…М-20a: эпизоды
# цели модели по контракту, склейка контракта, evaluate_alerts с окном 0…168 ч.
# Своя реализация совпадений здесь была до 22.09.2026 и дала 23,1 ч вместо
# канонических 16,7 — окно 720 ч, без порога длительности, один коллектор на префикс.
import model_failure  # noqa: E402
from check_metrics_report import ВЕРХ_ОКНА_Ч, ОКНО_ДО, ОКНО_ОТ  # noqa: E402
from predictive_metrics import evaluate_alerts  # noqa: E402

ROWS = ("М-09", "М-10", "М-11", "М-12", "М-13")

# Время в score.json без зоны — это московское время выгрузки: срез
# «2026-06-30T23:59:59» worker кладёт в pred.run.as_of как 23:59:59+03.
МОСКВА = ZoneInfo("Europe/Moscow")

# Предупреждение модели приходит на префикс тега, инцидент — на коллектор.
# Префикс кладём на ВСЕ коллекторы, где у него есть каналы, как в
# check_metrics_report.замер: 798 и 163 ведут к двум коллекторам каждый.
ПРЕФИКС_КОЛЛЕКТОРЫ = """
SELECT DISTINCT split_part(tag, '-', 1) AS pfx, collector_id
  FROM smvu.channel_collector WHERE collector_id IS NOT NULL
"""

# Что модулю заявок нужно в схеме. Проверяем колонки, а не таблицы: таблица
# maint.notification есть с 001_assets.sql, но без forecast_id и due_at модуль
# автозаявок на ней не собрать.
REQUIRED_COLUMNS = (
    "maint.notification.forecast_id",
    "maint.notification.due_at",
    "maint.notification.source_system",
    "maint.notification.long_text",
    "maint.work_order.notification_id",
    "maint.work_order.order_type_id",
    "maint.work_order.activity_type_id",
    "pred.forecast.horizon_h",
    "pred.forecast.run_id",
    "pred.run.as_of",
)

# ------------------------------------------------------------------ М-10, М-13: логика
#
# До 22.09.2026 М-13 сравнивала due_at с as_of + horizon_h — той же формулой,
# по которой order_rules.срок() этот due_at и поставил. Такая проверка зелёная
# по построению: 259 из 259, запас 8…12 ч, и так при любом сроке и любом
# горизонте (ревью Codex 22.09.2026, строка М-13 — gap). Теперь срок сравнивается
# не с формулой, а с данными: с медианой упреждения пойманных отказов, которую
# считает методика М-18…М-20a (упреждение() ниже).


def _момент(текст):
    t = datetime.fromisoformat(текст)
    return t if t.tzinfo else t.replace(tzinfo=МОСКВА)


def read_score(path):
    """Выдача модели из score.json: версия, горизонт, открытые и все предупреждения."""
    д = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "model": д["model_version"],
        "horizon_h": int(д["horizon_h"]),
        # (pfx, момент открытия, та же строка как в файле): строкой момент
        # входит в ключ заявки warn:<pfx>:<открытие>:<участок> (миграция 037).
        "open": [
            (str(к["pfx"]), _момент(к["warning_opened_at"]), к["warning_opened_at"])
            for к in д["collectors"]
            if к.get("warning_open")
        ],
        "alerts": [(str(а["pfx"]), _момент(а["t"])) for а in д.get("alerts") or []],
    }


def warnings_without_order(open_, keys, покрытые=frozenset()):
    """Открытые предупреждения, у которых нет заявки со своим ключом.

    open_ — [(pfx, opened_at, строка открытия)], keys — source_key автозаявок.
    Ключ заявки по предупреждению — warn:<pfx>:<открытие>:<участок>; участков
    у предупреждения бывает несколько, поэтому сверяем начало ключа.

    покрытые — пары (pfx, строка открытия), которым worker своих заявок не завёл
    по правилу Ф-48: на КАЖДОМ участке тройки предупреждения стоит живая заявка.
    Считает их check_m10 (ПОКРЫТО_ЖИВЫМИ) тем же условием, что worker.
    """
    return [
        (pfx, opened)
        for pfx, opened, строка in open_
        if (pfx, строка) not in покрытые
        and not any(k.startswith(f"warn:{pfx}:{строка}:") for k in keys)
    ]


# Ф-48 (решение Славы 26.09.2026): worker не заводит заявку на участок, где стоит
# живая заявка — не закрыта, срок позже среза прогона (order_rules.ЖИВЫЕ). Своих
# заявок у предупреждения нет законно, только если так было на КАЖДОМ участке его
# тройки. Тройка — снимок pred.warning_order_selection (MOS-184), тот же, что читает
# worker; срез — последнего удачного прогона, тот же момент, с которым worker
# сравнивал срок. Нет снимка или пустой — покрытым не считаем.
ПОКРЫТО_ЖИВЫМИ = """
WITH тройка AS (
    SELECT key::int AS section_id
      FROM pred.warning_order_selection s, jsonb_object_keys(s.plan) AS key
     WHERE s.pfx = $1 AND s.opened_at = $2
), срез AS (
    SELECT max(as_of) AS as_of FROM pred.run WHERE status = 'done'
)
SELECT count(*) > 0 AND bool_and(EXISTS (
           SELECT 1 FROM maint.notification n
             JOIN ref.object_xref x ON x.func_location_id = n.func_location_id
            WHERE x.section_id = т.section_id
              AND n.status IN ('OPEN', 'IN_PROCESS')
              AND n.due_at > (SELECT as_of FROM срез)))
  FROM тройка т
"""


def orders_per_warning(open_, orders, top):
    """Лишние заявки и заявки без одного наряда у открытых предупреждений.

    orders — [(source_key, нарядов)] автозаявок warn:…; top — потолок участков
    на предупреждение (ref.app_setting.order_top_sections_per_object).
    Возвращает (предупреждения с числом заявок вне 1…top, ключи заявок, у
    которых нарядов не ровно один). Второе ловит «INSERT … SELECT без наряда»:
    нет кода вида работ в справочнике — заявка есть, наряда нет, ошибки нет.
    """
    вне = []
    for pfx, opened, строка in open_:
        n = sum(1 for k, _ in orders if k.startswith(f"warn:{pfx}:{строка}:"))
        if n and not 1 <= n <= top:
            вне.append((pfx, n))
    не_один = [k for k, нарядов in orders if нарядов != 1]
    return вне, не_один


def collectors_without_order(open_, bridge, last_order):
    """Справочно: коллекторы под предупреждением без автозаявки после открытия.

    Так М-10 судила до 22.09.2026, и так засчитывалась заявка, которую родил
    порог вероятности, а не предупреждение: у префикса 418 (коллектор 11)
    «заявкой» считалась 4490 на участке 477:4 с p = 0,650 > 0,63.
    """
    return sorted(
        {
            bridge.get(pfx)
            for pfx, opened, _ in open_
            if not (
                bridge.get(pfx) in last_order and last_order[bridge.get(pfx)] >= opened
            )
        },
        key=str,
    )


def share_after(leads, reaction_h):
    """Доля пойманных отказов, случившихся СТРОГО позже срока работ."""
    return sum(1 for x in leads if x > reaction_h) / len(leads) if leads else 0.0


async def упреждение(conn, score):
    """Упреждения пойманных отказов, ч — методикой М-18…М-20a (MOS-167).

    Эпизоды цели модели по контракту failure.v3, склейка контракта по коллектору,
    окно ОКНО_ОТ…ОКНО_ДО, evaluate_alerts(…, 0, ВЕРХ_ОКНА_Ч). На стенде 22.09.2026:
    716 эпизодов -> 169 инцидентов, 170 предупреждений на коллекторах, 128
    попаданий, медиана 16,7 ч — то же, что check_metrics_report.py в PR #4.
    Возвращает (метрики evaluate_alerts, число инцидентов).
    """
    к = model_failure.загрузить_контракт()
    от = datetime.fromisoformat(ОКНО_ОТ).replace(tzinfo=МОСКВА)
    до = datetime.fromisoformat(ОКНО_ДО).replace(tzinfo=МОСКВА)
    rows = await conn.fetch(
        model_failure.ЭПИЗОДЫ_МОДЕЛИ,
        от,
        до,
        к["failure_values"],
        к["episode"]["min_duration_seconds"],
        к["model_version"],
    )
    коллектор_канала = {
        r["channel_id"]: r["collector_id"]
        for r in rows
        if r["collector_id"] is not None
    }
    инциденты = [
        (k, t)
        for k, t in model_failure.инциденты(
            [(r["channel_id"], r["started_at"]) for r in rows],
            коллектор_канала,
            к["incident"]["merge_minutes"],
        )
        if от < t < до
    ]
    префикс = {}
    for r in await conn.fetch(ПРЕФИКС_КОЛЛЕКТОРЫ):
        префикс.setdefault(r["pfx"], set()).add(r["collector_id"])
    на_коллекторах = [
        (f"obj:{c}", t) for p, t in score["alerts"] for c in sorted(префикс.get(p, ()))
    ]
    return evaluate_alerts(на_коллекторах, инциденты, 0, ВЕРХ_ОКНА_Ч), len(инциденты)


# ------------------------------------------------------------------ проверки


async def check_m09(conn):
    """Модуль заявок — отдельная часть продукта: своя схема и свои строки."""
    found = {
        r[0]
        for r in await conn.fetch(
            "SELECT table_schema||'.'||table_name||'.'||column_name "
            "FROM information_schema.columns "
            "WHERE table_schema||'.'||table_name||'.'||column_name = ANY($1::text[])",
            list(REQUIRED_COLUMNS),
        )
    }
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        return False, (
            f"схема модуля неполна: {len(found)} из {len(REQUIRED_COLUMNS)} колонок, "
            f"нет {', '.join(missing)}"
        )
    total = await conn.fetchval("SELECT count(*) FROM maint.notification")
    if total == 0:
        return False, (
            f"{len(found)} из {len(REQUIRED_COLUMNS)} колонок на месте, "
            "но в maint.notification 0 заявок: пустая таблица модуля не образует"
        )
    return True, (
        f"{len(found)} из {len(REQUIRED_COLUMNS)} колонок модуля на месте, "
        f"в maint.notification {total} заявок"
    )


async def check_m10(conn, score):
    """Открытое предупреждение модели превращается в заявку без диспетчера.

    Условие — у КАЖДОГО открытого предупреждения (pfx, момент открытия) из
    score.json есть автозаявка с его ключом warn:<pfx>:<открытие>:… Прежняя
    проверка считала только source_system='forecast' и непустой forecast_id,
    а следующая за ней — заявку на коллекторе; обе засчитывали заявку, которую
    родил порог вероятности, а не предупреждение. Счёт по коллектору печатается
    справочно: разница двух чисел — ровно то, что чинит MOS-180.
    """
    base_ok, base_text = await _m10_source(conn)
    if not base_ok:
        return False, base_text
    if score is None:
        return False, (
            f"{base_text}; открытые предупреждения сверить не с чем — "
            "не задан SCORE_JSON (check-all.sh берёт его со стенда по STAND_SSH)"
        )
    if not score["open"]:
        return False, (
            f"{base_text}; в score.json 0 открытых предупреждений — "
            "проверять превращение в заявку не на чем"
        )
    orders = [
        (r["source_key"], r["нарядов"])
        for r in await conn.fetch(
            "SELECT n.source_key, (SELECT count(*) FROM maint.work_order w "
            "                       WHERE w.notification_id = n.id) AS нарядов "
            "FROM maint.notification n "
            "WHERE n.source_system = 'forecast' AND n.source_key LIKE 'warn:%'"
        )
    ]
    keys = [k for k, _ in orders]
    покрытые = {
        (pfx, строка)
        for pfx, opened, строка in score["open"]
        if not any(k.startswith(f"warn:{pfx}:{строка}:") for k in keys)
        and await conn.fetchval(ПОКРЫТО_ЖИВЫМИ, pfx, opened)
    }
    потери = warnings_without_order(score["open"], keys, покрытые)
    top = await conn.fetchval(
        "SELECT value FROM ref.app_setting WHERE key = 'order_top_sections_per_object'"
    )
    if top is None:
        return (
            False,
            "нет настройки order_top_sections_per_object: сколько заявок на предупреждение, не сказано",
        )
    вне, не_один = orders_per_warning(score["open"], orders, int(top))

    bridge = {
        r["pfx"]: r["collector_id"]
        for r in await conn.fetch("SELECT pfx, collector_id FROM pred.pfx_collector")
    }
    # Только заявки от прогнозов ЭТОЙ модели: заявки заглушки stub-0.1 от
    # 17…21.09.2026 несут настоящее сентябрьское reported_at, и оно «позже»
    # любого июньского открытия.
    last_order = {
        r["collector_id"]: r["last"]
        for r in await conn.fetch(
            f"SELECT sc.collector_id, max(n.reported_at) AS last "
            f"FROM maint.notification n "
            f"JOIN pred.forecast f ON f.forecast_id = n.forecast_id "
            f"JOIN pred.run r ON r.run_id = f.run_id "
            f"JOIN ({УЧАСТКИ_КОЛЛЕКТОРА}) sc ON sc.section_id = f.section_id "
            f"WHERE n.source_system = 'forecast' AND r.model_version = $1 "
            f"GROUP BY sc.collector_id",
            score["model"],
        )
    }
    коллекторов = len({bridge.get(p) for p, _, _ in score["open"]})
    без_коллектора = collectors_without_order(score["open"], bridge, last_order)
    суть = (
        f"модель {score['model']}: по предупреждению без заявки {len(потери)} из "
        f"{len(score['open'])} (ключ warn:<pfx>:<открытие>, заявок с таким ключом "
        f"{len(keys)}, без своих из-за живой на всей тройке {len(покрытые)}); "
        f"справочно по коллектору {len(без_коллектора)} из {коллекторов}"
    )
    суть += (
        f"; заявок на предупреждение вне 1…{int(top)}: {len(вне)}, заявок warn: "
        f"не с одним нарядом: {len(не_один)}"
    )
    беды = []
    if потери:
        беды.append(
            "без заявки: "
            + ", ".join(f"{p} открыто {t:%d.%m %H:%M}" for p, t in потери)
        )
    if вне:
        беды.append("вне 1…top: " + ", ".join(f"{p} — {n}" for p, n in вне))
    if не_один:
        беды.append("не с одним нарядом: " + ", ".join(не_один[:5]))
    if беды:
        return False, f"{суть}; {'; '.join(беды)}"
    return True, f"{суть}; {base_text}"


async def _m10_source(conn):
    """Заявки в базе вообще заводит расчёт, а не человек."""
    row = await conn.fetchrow(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE source_system = 'forecast') AS forecast_src, "
        "count(*) FILTER (WHERE source_system = 'forecast' "
        "                 AND forecast_id IS NOT NULL) AS auto "
        "FROM maint.notification"
    )
    if row["total"] == 0:
        return False, "в maint.notification 0 заявок: расчёт не завёл ни одной"
    if row["auto"] == 0:
        return False, (
            f"{row['total']} заявок, из них с source_system='forecast' "
            f"{row['forecast_src']}, с заполненным forecast_id 0 — "
            "ни одну не родил расчёт"
        )
    # Расхождение forecast_src и auto означало бы дыру в CHECK из 001_assets.sql
    # (source_system <> 'forecast' OR forecast_id IS NOT NULL), поэтому печатаем
    # оба числа: сошлись — заодно доказали, что ограничение работает.
    return True, (
        f"{row['total']} заявок, из них {row['forecast_src']} с "
        f"source_system='forecast', и у всех {row['auto']} заполнен forecast_id"
    )


async def check_m11(conn):
    """У автозаявки заполнены объект, вид работ, срок и обоснование."""
    row = await conn.fetchrow(
        "SELECT count(*) AS auto, "
        "count(*) FILTER (WHERE n.func_location_id IS NOT NULL "
        "                 OR n.equipment_id IS NOT NULL) AS with_object, "
        "count(*) FILTER (WHERE n.due_at IS NOT NULL) AS with_due, "
        "count(*) FILTER (WHERE coalesce(btrim(n.long_text), '') <> '') AS with_reason, "
        "count(*) FILTER (WHERE w.id IS NOT NULL) AS with_work_type "
        "FROM maint.notification n "
        # Вид работ берём через заказ ТОиР: order_type_id и activity_type_id там
        # NOT NULL и ссылаются на ref.order_type / ref.activity_type, то есть
        # сам факт заказа доказывает «из справочника, а не текстом».
        "LEFT JOIN LATERAL (SELECT wo.id FROM maint.work_order wo "
        "                    JOIN ref.order_type ot ON ot.id = wo.order_type_id "
        "                    JOIN ref.activity_type at ON at.id = wo.activity_type_id "
        "                   WHERE wo.notification_id = n.id LIMIT 1) w ON true "
        "WHERE n.source_system = 'forecast'"
    )
    auto = row["auto"]
    if auto == 0:
        return False, "0 автозаявок: проверять заполненность не на чем"
    parts = (
        f"объект у {row['with_object']}, срок у {row['with_due']}, "
        f"обоснование у {row['with_reason']}, "
        f"вид работ из справочника у {row['with_work_type']}"
    )
    bad = [
        k
        for k in ("with_object", "with_due", "with_reason", "with_work_type")
        if row[k] != auto
    ]
    if bad:
        return False, f"из {auto} автозаявок: {parts} — заполнены не все четыре поля"
    return True, f"{auto} автозаявок, {parts}"


async def check_m12(conn):
    """Связь заявки с прогнозом есть в базе — ПОЛОВИНА строки М-12.

    Строка М-12 требует двух вещей: связи в данных и перехода в обе стороны
    через API и на экране (`GET /api/orders/{id}` отдаёт forecast_id,
    `GET /api/forecasts/{id}` отдаёт order_ids). Здесь проверяется только первая:
    запрос смотрит внешний ключ, а не ответ метода. На 17.09.2026 метод заявок
    отдаёт пустой список, потому что Q6.5 не написана, и зелёная строка М-12
    здесь НЕ означает, что приёмщик пройдёт переход из карточки в карточку.
    Вторую половину дописывать сюда, когда Q6.5 сдана.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS auto, "
        "count(f.forecast_id) AS linked, "
        "count(DISTINCT f.forecast_id) AS forecasts "
        "FROM maint.notification n "
        # LEFT JOIN, а не JOIN: обычный JOIN выбросил бы заявки с битой ссылкой
        # и оставил зелёный счётчик на уменьшившемся знаменателе.
        "LEFT JOIN pred.forecast f ON f.forecast_id = n.forecast_id "
        "WHERE n.source_system = 'forecast'"
    )
    auto = row["auto"]
    if auto == 0:
        return False, "0 автозаявок: ссылаться на прогноз некому"
    if row["linked"] != auto:
        return False, (
            f"{auto} автозаявок, из них ссылка ведёт на существующий прогноз "
            f"только у {row['linked']}: {auto - row['linked']} ссылок битые"
        )
    return True, (
        f"{auto} автозаявок, у всех {row['linked']} forecast_id ведёт на строку "
        f"pred.forecast; обратно эти заявки собираются на {row['forecasts']} прогнозах"
    )


def _сроки(rows, медиана, leads):
    """Беды и сводка по одной группе заявок: просрочка с рождения и медиана."""
    реакция = [(r["due_at"] - r["as_of"]).total_seconds() / 3600 for r in rows]
    худшая = max(реакция)
    просрочены = sum(1 for x in реакция if x <= 0)
    суть = (
        f"{len(rows)} заявок: срок через {min(реакция):.1f}…{худшая:.1f} ч после as_of "
        f"прогона, отказ позже худшего срока у {sum(1 for x in leads if x > худшая)} "
        f"из {len(leads)} — {share_after(leads, худшая):.1%}"
    )
    беды = []
    if просрочены:
        беды.append(
            f"срок не позже as_of прогона у {просрочены} из {len(rows)}: "
            "заявка родилась просроченной"
        )
    if худшая > медиана:
        беды.append(
            f"худший срок {худшая:.1f} ч позже медианы {медиана} ч: "
            "бригада чаще приходит к уже случившемуся отказу"
        )
    return беды, суть


async def check_m13(conn, score):
    """Срок работ наступает раньше настоящего отказа, а не раньше формулы.

    Судим автозаявки по предупреждению (ключ warn:…, MOS-180) от модели из
    score.json. Срок меряем от as_of прогона, заведшего заявку, — от момента,
    когда продукт узнал о риске.
    1. Срок позже as_of прогона. Срок раньше — заявка родилась просроченной.
    2. Худший срок (due_at − as_of) не позже медианы упреждения пойманных
       отказов (упреждение(), методика М-18…М-20a). Отдельного порога нет.
    Горизонт здесь не судим (решение 27.09.2026): order_rules.срок() его не
    берёт с c7c07db, а 24 в прогнозе против 720 у модели v3 — дефект М-20
    (MOS-219), его ловит check_metrics.py. Старую формулу «срез + горизонт»
    ловит условие 2 — самопроверка в demo().
    Прежние заявки той же модели — по суточному порогу, до ключа warn: — не
    судим, а печатаем справочно: их правило больше не работает, а сами они в
    базе остаются. Без этого разделения строка краснела бы навсегда от 35 заявок
    прогонов 489 и 501, которые правка уже не заводит. Удалять их или нет —
    решение о данных, а не проверки.
    """
    if score is None:
        return False, (
            "горизонт модели и её предупреждения взять неоткуда — не задан "
            "SCORE_JSON (check-all.sh берёт его со стенда по STAND_SSH)"
        )
    rows = await conn.fetch(
        "SELECT n.due_at, n.source_key, r.as_of, f.horizon_h "
        "FROM maint.notification n "
        "JOIN pred.forecast f ON f.forecast_id = n.forecast_id "
        "JOIN pred.run r ON r.run_id = f.run_id "
        "WHERE n.source_system = 'forecast' AND r.model_version = $1",
        score["model"],
    )
    m0, инцидентов = await упреждение(conn, score)
    if not m0["tp"]:
        return False, (
            f"ни одно из {len(score['alerts'])} предупреждений модели не поймало "
            f"отказ из {инцидентов} — судить о сроке не по чему"
        )
    leads, медиана = m0["lead_hours"], m0["median_lead_hours"]
    текущие = [r for r in rows if (r["source_key"] or "").startswith("warn:")]
    прежние = [r for r in rows if r not in текущие]

    голова = (
        f"модель {score['model']}, медиана упреждения {медиана} ч ({m0['tp']} "
        f"пойманных из {инцидентов} инцидентов, окно 0…{ВЕРХ_ОКНА_Ч} ч, методика "
        f"М-18…М-20a)"
    )
    справка = ""
    if прежние:
        беды_п, суть_п = _сроки(прежние, медиана, leads)
        справка = f"; справочно прежние (не warn:) {суть_п}" + (
            f" — {'; '.join(беды_п)}" if беды_п else ""
        )
    if not текущие:
        return False, f"{голова}; автозаявок по предупреждению (warn:) 0{справка}"
    беды, суть = _сроки(текущие, медиана, leads)
    текст = f"{голова}; по предупреждению {суть}"
    if беды:
        return False, f"{текст}; {'; '.join(беды)}{справка}"
    return True, текст + справка


CHECKS = (
    ("М-09", check_m09),
    ("М-10", check_m10),
    ("М-11", check_m11),
    ("М-12", check_m12),
    ("М-13", check_m13),
)
NEEDS_SCORE = (check_m10, check_m13)


async def run_checks(dsn, score):
    conn = await asyncpg.connect(dsn)
    try:
        out = []
        for name, fn in CHECKS:
            try:
                ok, text = await (fn(conn, score) if fn in NEEDS_SCORE else fn(conn))
            except asyncpg.PostgresError as e:
                # Нет таблицы или схемы — это СБОЙ конкретной строки, а не
                # падение всей проверки: остальные четыре строки всё равно надо
                # предъявить на приёмке.
                ok, text = False, f"база отказала: {(str(e).splitlines() or [type(e).__name__])[0]}"
            out.append((name, ok, text))
        return out
    finally:
        await conn.close()


def report(results):
    for name, ok, text in results:
        print(f"{name} {'OK' if ok else 'СБОЙ'} {text}")
    return 0 if all(ok for _, ok, _ in results) else 1


def fail_all(reason):
    return report([(name, False, reason) for name in ROWS])


# ------------------------------------------------------------------ самопроверка


def demo(verbose=True):
    """Логика М-10 и М-13 без живой базы."""
    t = datetime(2026, 6, 10, 12, tzinfo=МОСКВА)
    h = timedelta(hours=1)

    # М-10: заявка по ключу своего предупреждения; чужой момент открытия и
    # чужой префикс не в счёт, участков у предупреждения может быть несколько.
    открытые = [("889", t, "2026-06-10T08:36:56"), ("15", t, "2026-06-17T23:59:59")]
    ключи = [
        "warn:889:2026-06-10T08:36:56:11",
        "warn:889:2026-06-10T08:36:56:2477",
        "warn:15:2026-06-01T00:00:00:401",
        "warn:150:2026-06-17T23:59:59:9",
    ]
    assert warnings_without_order(открытые, ключи) == [("15", t)]
    # Ф-48: у 15 своих заявок нет, потому что вся его тройка под живыми заявками.
    assert warnings_without_order(открытые, ключи, {("15", "2026-06-17T23:59:59")}) == []
    # Сколько заявок и нарядов: у 889 четыре заявки при потолке 3, у одной нет наряда.
    заявки = [(k, 1) for k in ключи] + [
        ("warn:889:2026-06-10T08:36:56:12", 1),
        ("warn:889:2026-06-10T08:36:56:13", 0),
    ]
    вне, не_один = orders_per_warning(открытые, заявки, 3)
    assert вне == [("889", 4)] and не_один == ["warn:889:2026-06-10T08:36:56:13"], (
        вне,
        не_один,
    )
    # Справочно по коллектору: заявка до открытия не считается, префикс без моста — потеря.
    bridge = {"15": 6, "257": 3355}
    по_коллектору = [("15", t, ""), ("257", t, ""), ("999", t, "")]
    assert collectors_without_order(по_коллектору, bridge, {6: t - h, 3355: t + h}) == [
        6,
        None,
    ]

    # М-13: отказ ровно в срок — не успели, «строго позже». Сами упреждения
    # считает evaluate_alerts, её самопроверка — в code/predictive_metrics.py.
    assert share_after([5.0, 12.0, 30.0], 12.0) == 1 / 3
    assert share_after([], 12.0) == 0.0
    # Срок по старой формуле «срез + 720 ч» обязан краснеть условием 2, срок
    # через 16 ч при медиане 16,3 — нет; горизонт в строке не участвует.
    срез = datetime(2026, 6, 1, tzinfo=МОСКВА)
    по_старой = [{"due_at": срез + timedelta(hours=720), "as_of": срез, "horizon_h": 720}]
    по_новой = [{"due_at": срез + timedelta(hours=16), "as_of": срез, "horizon_h": 24}]
    assert _сроки(по_старой, 16.3, [5.0, 30.0])[0], "старая формула прошла М-13"
    assert _сроки(по_новой, 16.3, [5.0, 30.0])[0] == [], "срок 16 ч при медиане 16,3 — беда"

    if verbose:
        print("демо М-10: ключ чужого открытия и чужого префикса не засчитан")
        print("демо М-13: отказ ровно в срок не предотвращён")
        print("OK")


def main(argv):
    # логика сравнения обязана быть цела до любого похода в базу
    demo(verbose="--demo" in argv)
    if "--demo" in argv:
        return 0
    if asyncpg is None:
        return fail_all("не установлен asyncpg — тот же пакет, что у backend/app/db.py")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return fail_all("не задана переменная DATABASE_URL, подключаться не к чему")
    путь = os.environ.get("SCORE_JSON")
    try:
        score = read_score(путь) if путь else None
    except (OSError, ValueError, KeyError) as e:
        return fail_all(f"score.json не читается ({путь}): {e}")
    try:
        results = asyncio.run(run_checks(dsn, score))
    except (OSError, ValueError, asyncpg.PostgresError, asyncio.TimeoutError) as e:
        return fail_all(f"нет связи с базой: {(str(e).splitlines() or [type(e).__name__])[0]}")
    return report(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
