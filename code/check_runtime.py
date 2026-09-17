#!/usr/bin/env python3
"""Приёмка времени расчёта: строки М-21 и НФ-72.

М-21 — время формирования прогноза меньше 5 минут на продуктивном объёме.
НФ-72 — инференс по одному объекту меньше 5 минут.

**Что этот скрипт доказывает и чего не доказывает.** Он не запускает расчёт,
а читает журнал прогонов `pred.run` — тот самый, который расчёт заполняет сам
шестью колонками `ms_*`. Это доказательство «расчёт укладывался», а не «расчёт
уложится прямо сейчас»: чтобы получить второе, надо запустить прогон, а прогон
пишет в базу прогнозы и заявки, и приёмочная проверка такого делать не должна.
Поэтому скрипт требует, чтобы улов был свежим и настоящим: прогоны должны быть
по ВСЕМУ парку, а не по обрезанной выборке, и у них должны быть заполнены все
шесть стадий. Прогон с пустыми `ms_*` — это не быстрый прогон, а недомеренный.

Как получить улов, если скрипт красный:
    docker exec moskollektor-api-1 python -m app.worker.run --as-of 2026-06-30T23:59:59+03:00
    docker exec moskollektor-api-1 python -m app.worker.run --as-of ... --limit 1

Проверка по умолчанию отказывает. Нет DATABASE_URL, нет таблицы, ноль прогонов —
всё это СБОЙ, а не молчаливый ноль.

Запуск:  DATABASE_URL=postgresql://... python3 code/check_runtime.py
Самопроверка без базы:  python3 code/check_runtime.py --demo
"""

import asyncio
import os
import sys

try:
    import asyncpg
except ImportError:  # пакет живёт в образе бэкенда, локально его может не быть
    asyncpg = None

ROWS = ("М-21", "НФ-72")

# Постановка: «время формирования прогноза менее 5 минут». Пять минут — это 300
# секунд, и меньше значит строго меньше.
НОРМАТИВ_С = 300.0

# Сколько прогонов по полному парку должно лежать в журнале. Три, а не один:
# один прогон не отличает «укладывается» от «повезло». Число из задачи Q3.5.
ПРОГОНОВ_НАДО = 3

СТАДИИ = (
    "ms_fetch",
    "ms_aggregate",
    "ms_features",
    "ms_inference",
    "ms_explain",
    "ms_write",
)


def длительность_с(строка):
    """Сколько секунд занял прогон по сумме шести стадий.

    None, если хоть одна стадия не замерена: сумма пяти стадий из шести — это
    не длительность прогона, а меньшее число, выдающее себя за неё. Разница
    здесь решающая, потому что мы сравниваем с нормативом.
    """
    if any(строка[к] is None for к in СТАДИИ):
        return None
    return sum(строка[к] for к in СТАДИИ) / 1000.0


def укладывается(секунд):
    """Строго меньше норматива. Ровно 300,0 секунды норматив не закрывает."""
    return секунд is not None and секунд < НОРМАТИВ_С


ПОЛНЫЕ_ПРОГОНЫ = """
SELECT r.run_id, r.as_of, r.objects_total, r.objects_scored,
       r.ms_fetch, r.ms_aggregate, r.ms_features, r.ms_inference,
       r.ms_explain, r.ms_write
  FROM pred.run r
 WHERE r.status = 'done'
   AND r.objects_total = (SELECT count(*) FROM ref.object_xref)
 ORDER BY r.run_id DESC
 LIMIT $1
"""

ОДИН_ОБЪЕКТ = """
SELECT r.run_id, r.as_of, r.objects_total,
       r.ms_fetch, r.ms_aggregate, r.ms_features, r.ms_inference,
       r.ms_explain, r.ms_write
  FROM pred.run r
 WHERE r.status = 'done' AND r.objects_total = 1
 ORDER BY r.run_id DESC
 LIMIT 1
"""


async def check_m21(conn):
    """Полный расчёт по всему парку укладывается в 5 минут, и так три раза."""
    участков = await conn.fetchval("SELECT count(*) FROM ref.object_xref")
    строки = await conn.fetch(ПОЛНЫЕ_ПРОГОНЫ, ПРОГОНОВ_НАДО)
    if len(строки) < ПРОГОНОВ_НАДО:
        return False, (
            f"прогонов по всем {участков} участкам в журнале {len(строки)}, "
            f"надо {ПРОГОНОВ_НАДО}: один прогон не отличает «укладывается» от «повезло»"
        )

    времена = [(с["run_id"], длительность_с(с)) for с in строки]
    недомерено = [rid for rid, t in времена if t is None]
    if недомерено:
        return False, (
            f"у прогонов {', '.join(map(str, недомерено))} замерены не все шесть "
            f"стадий — сумма неполного профиля не является длительностью прогона"
        )

    худший_id, худший = max(времена, key=lambda п: п[1])
    лучший = min(t for _, t in времена)
    if not укладывается(худший):
        return False, (
            f"худший прогон {худший_id}: {худший:.1f} с при нормативе {НОРМАТИВ_С:.0f} с "
            f"на {участков} участках"
        )

    доля = {к: max(с[к] for с in строки) for к in СТАДИИ}
    самая = max(доля, key=доля.get)
    return True, (
        f"{len(строки)} прогона по {участков} участкам, худший {худший_id}: "
        f"{худший:.1f} с, лучший {лучший:.1f} с при нормативе {НОРМАТИВ_С:.0f} с; "
        f"дороже всех стадия {самая} — {доля[самая] / 1000:.1f} с"
    )


async def check_nf72(conn):
    """Расчёт по одному объекту укладывается в 5 минут."""
    с = await conn.fetchrow(ОДИН_ОБЪЕКТ)
    if с is None:
        return False, (
            "в журнале нет ни одного прогона по одному объекту: запустите "
            "python -m app.worker.run --as-of ... --limit 1"
        )
    секунд = длительность_с(с)
    if not укладывается(секунд):
        подробность = (
            "не все шесть стадий замерены" if секунд is None else f"{секунд:.1f} с"
        )
        return False, (
            f"прогон {с['run_id']} по одному объекту: {подробность} "
            f"при нормативе {НОРМАТИВ_С:.0f} с"
        )
    return True, (
        f"прогон {с['run_id']} по одному объекту: {секунд:.1f} с "
        f"при нормативе {НОРМАТИВ_С:.0f} с"
    )


CHECKS = (("М-21", check_m21), ("НФ-72", check_nf72))


async def run_checks(dsn):
    conn = await asyncpg.connect(dsn)
    try:
        out = []
        for name, fn in CHECKS:
            try:
                ok, text = await fn(conn)
            except asyncpg.PostgresError as e:
                ok, text = False, f"база отказала: {str(e).splitlines()[0]}"
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


def demo(verbose=True):
    """Проверка арифметики без базы: что считается длительностью и что нормативом."""
    полный = dict(
        ms_fetch=4609,
        ms_aggregate=3094,
        ms_features=24402,
        ms_inference=66,
        ms_explain=73,
        ms_write=463,
    )
    assert abs(длительность_с(полный) - 32.707) < 1e-9, длительность_с(полный)
    assert укладывается(длительность_с(полный))

    # Ровно норматив — не проходит: постановка требует «менее 5 минут».
    assert not укладывается(300.0)
    assert укладывается(299.999)

    # Недомеренный прогон не выдаёт себя за быстрый. Это не придирка: прогон
    # с пустой ms_features дал бы 8,3 секунды вместо 32,7 и прошёл бы норматив
    # с запасом, хотя самой дорогой стадии в нём просто не замерили.
    кривой = dict(полный, ms_features=None)
    assert длительность_с(кривой) is None
    assert not укладывается(длительность_с(кривой))
    assert sum(v for v in кривой.values() if v) / 1000 < 8.4, "пример потерял смысл"

    if not verbose:
        return
    print("демо М-21: 4609+3094+24402+66+73+463 мс = 32,7 с — норматив 300 с закрыт")
    print("демо М-21: ровно 300,0 с норматив НЕ закрывает, нужно строго меньше")
    print("демо М-21: прогон без ms_features даёт 8,3 с и обязан считаться СБОЕМ")
    print("OK")


def main(argv):
    # арифметика обязана быть цела до любого похода в базу
    demo(verbose="--demo" in argv)
    if "--demo" in argv:
        return 0
    if asyncpg is None:
        return fail_all("не установлен asyncpg — тот же пакет, что у backend/app/db.py")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return fail_all("не задана переменная DATABASE_URL, подключаться не к чему")
    try:
        results = asyncio.run(run_checks(dsn))
    except (OSError, ValueError, asyncpg.PostgresError, asyncio.TimeoutError) as e:
        return fail_all(f"нет связи с базой: {str(e).splitlines()[0]}")
    return report(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
