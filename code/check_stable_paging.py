#!/usr/bin/env python3
"""Постраничность держит множество без повторов и пропусков. Задача MOS-223
(Q4.17), строки приёмки М-06, М-16.

**Нашла сессия 98 при сверке хешей для MOS-221, 23.09.2026.** Один и тот же
`GET /api/orders` под `ods1`, повторённый на неизменном стенде, отдавал на
странице разный набор `id` при одинаковом `total`. Причина — `ORDER BY
n.due_at` без второго ключа: у заявок с одинаковым сроком Postgres волен
отдавать их в любом порядке, и `offset` пропускает один набор строк, а другой
дублирует.

**Что проверяем.** Не код ответа и не факт разбора JSON — проходим все
страницы подряд (`limit`/`offset` до конца) и собираем `id` в множество.
Если сортировка нестабильна, между двумя проходами (а иногда и внутри
одного — Postgres может сменить план между запросами страниц) какие-то `id`
пропадут, какие-то задвоятся, и размер множества станет меньше `total`.
Стабильная сортировка даёт множество размером ровно `total` при любом числе
проходов.

**Пять методов, а не пять из задачи Jira — другой состав.** `GET
/api/orders`, `GET /api/forecasts`, `GET /api/audit`, `GET
/api/objects/{id}/channels`, `GET /api/tech-events` (MOS-42) — у всех есть
`limit`/`offset`. **`GET /api/objects/{id}/readings` в задаче названа
пятой ошибочно: у метода нет ни `limit`, ни `offset` вовсе** (`from`/`to` —
обязательные даты окна, не постраничность) — grep по
`backend/app/api/objects.py` подтверждает, страницы у неё нет и стабилизировать
нечего; `tech-events` встал на её место. `GET /api/risks` тоже не в списке:
она отдаёт всё целиком, без параметров.

`GET /api/forecasts` и `GET /api/audit` берутся с фиксированным `to` в
прошлом — иначе журнал растёт за секунды многостраничного прохода (пульс раз
в час, планировщик — раз в несколько минут), и «пропуск» окажется ростом
данных, а не дефектом сортировки.

**Честно про audit и forecasts: проверили 23.09.2026 (я и 5e), повторов
ключа сортировки в проверенном окне нет.** У `(started_at, risk_rank)` в
`pred.forecast` за 22.09.2026 — ноль дублей (шире не мерили); у
`occurred_at` в `audit.user_action` в окне `to=2026-09-22` — тоже ноль
(живые совпадения есть, но позже этого окна: 23.09 18:52–18:56, `action_id`
11269/70, 11543/44, 12139/40, 13013/14 — нашла 5e). Эти две части строки
не доказывают сегодняшний дефект — добавленный столбец (`f.forecast_id`
третьим ключом у forecasts, `a.action_id` вторым у audit) стоит на будущее,
той же логикой, что уже стоит у `channels` (кончается `c.channel_id`).
Живой дефект здесь ловили только `orders` (было) и он же после фикса —
зелёная строка.

**У `/api/forecasts` `from`/`to` — дата, не момент времени, уже сузить
нельзя, а в одних сутках 108 035 строк.** Гонять их все — не в 108 запросов,
как при limit=1000, а лишняя нагрузка на общий стенд ради проверки, которой
для отлова нестабильности (если она появится) хватает меньшего. Здесь
`max_pages` останавливает проход раньше `total`: тогда проверяем не
«столько же id, сколько total», а «внутри пройденных страниц каждый id
встретился один раз» — слабее, но для сторожевой роли этого достаточно.

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k python3 code/check_stable_paging.py
    python3 code/check_stable_paging.py --selfcheck
"""
import json
import os
import subprocess
import sys

ROWS = "М-06, М-16"

# путь, ключ элемента, ключ id, лимит страницы, учётка, max_pages (None — до total)
МЕТОДЫ = [
    # limit=200 дефект orders не ловит: старый ORDER BY n.due_at дал 303 из 303,
    # хотя граница на 200 режет группу из 32 заявок с одинаковым сроком
    # (места 176-207) — совпадающие строки легли одинаково при LIMIT 200 и 400.
    # При limit=10 границ 30, и та же сортировка теряет 50 заявок (253 из 303).
    ("/api/orders", "items", "id", 10, "admin1", None),
    ("/api/forecasts?from=2026-09-22&to=2026-09-22", "items", "forecast_id", 1000, "admin1", 5),
    ("/api/audit?to=2026-09-22T00:00:00", "items", "action_id", 50, "admin1", None),
    # участок 2204 — крупнейший по числу каналов среди разобранных, 100 штук
    # (см. MOS-151 в backend/app/api/objects.py): маленький список не
    # заставит метод отдать больше одной страницы.
    ("/api/objects/2204/channels", "items", "channel_id", 10, "admin1", None),
    # MOS-42 (50): тот же дефект нашла и починила сама — ORDER BY read_time
    # без второго ключа, у read_time бывают повторы (несколько каналов пишут
    # в одну секунду). Второй ключ — m.journal_id, он же ключ id здесь.
    ("/api/tech-events", "items", "journal_id", 10, "admin1", None),
]


def множество_без_повторов(total, ids, частично):
    """Полный проход: столько же id в множестве, сколько заявлено total —
    меньше total значит в множестве есть пропуски и одновременно повторы:
    страницы в сумме отдают ровно total сырых строк (limit * число страниц
    плюс хвост), поэтому потерянный из-за повтора слот — это и есть
    пропавший id.

    Частичный проход (остановлен по max_pages раньше total): total здесь
    ничего не доказывает, проверяем только то, что можем — среди уже
    собранных id повторов нет.
    """
    distinct = set(ids)
    if частично:
        if len(distinct) != len(ids):
            return False, f"повтор внутри первых {len(ids)} строк ({len(ids) - len(distinct)} шт.) — сортировка нестабильна"
        return True, f"первые {len(ids)} id (из total {total}) — все разные, повторов нет"
    if len(ids) != total:
        return False, f"сырых строк по всем страницам {len(ids)}, а total {total} — прошли не весь набор"
    if len(distinct) != total:
        return False, f"уникальных id {len(distinct)} против total {total} — есть повтор, и он же пропуск"
    return True, f"{total} id, все разные, повторов и пропусков нет"


def curl(url, login):
    cmd = ["curl", "-s"]
    cmd += os.environ.get("CURL_OPTS", "").split()
    cmd += ["-H", f"X-User-Login: {login}", url]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    return json.loads(out)


def пройти_все_страницы(base, path, items_key, id_key, limit, login, max_pages):
    sep = "&" if "?" in path else "?"
    offset = 0
    ids = []
    total = None
    страниц = 0
    частично = False
    while True:
        тело = curl(f"{base}{path}{sep}limit={limit}&offset={offset}", login)
        if total is None:
            total = тело["total"]
        items = тело[items_key]
        if not items:
            break
        ids.extend(item[id_key] for item in items)
        offset += limit
        страниц += 1
        if max_pages is not None and страниц >= max_pages and offset < total:
            частично = True
            break
        if offset > total + limit:
            # Страховка от бесконечного цикла, если total сам оказался лживым —
            # это отдельный дефект (МОS-117), не этой строки, но зависать
            # проверка не должна ни при каком СБОЕ соседней строки.
            break
    return total, ids, частично


def _selfcheck():
    ok, текст = множество_без_повторов(5, [1, 2, 3, 4, 5], частично=False)
    assert ok, текст
    ok, текст = множество_без_повторов(5, [1, 2, 2, 4, 5], частично=False)
    assert not ok, "повтор внутри total обязан провалить проверку"
    ok, текст = множество_без_повторов(5, [1, 2, 3, 4], частично=False)
    assert not ok, "меньше total сырых строк — сама выборка неполная"
    ok, текст = множество_без_повторов(1000, [1, 2, 3], частично=True)
    assert ok, "частичный проход без повторов обязан пройти, хотя дошли не до total"
    ok, текст = множество_без_повторов(1000, [1, 2, 2], частично=True)
    assert not ok, "повтор внутри частичного прохода обязан провалить проверку"
    print("самопроверка ok: множество без повторов, повтор внутри total, неполная выборка, частичный проход")


def main():
    if "--selfcheck" in sys.argv:
        _selfcheck()
        return 0
    _selfcheck()
    base = os.environ.get("BASE_URL")
    if not base:
        print(f"{ROWS} СБОЙ: не задан BASE_URL")
        return 1

    всё_ок = True
    for path, items_key, id_key, limit, login, max_pages in МЕТОДЫ:
        try:
            total, ids, частично = пройти_все_страницы(base, path, items_key, id_key, limit, login, max_pages)
        except Exception as e:
            print(f"{ROWS} СБОЙ {path}: не прошли страницы — {type(e).__name__}: {e}")
            всё_ок = False
            continue
        ok, текст = множество_без_повторов(total, ids, частично)
        print(f"{ROWS} {'OK' if ok else 'СБОЙ'} {path}: {текст}")
        всё_ок &= ok

    return 0 if всё_ок else 1


if __name__ == "__main__":
    sys.exit(main())
