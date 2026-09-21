#!/usr/bin/env python3
"""Второй счёт замера 21.09.2026: отказы берём из базы стенда, а не из чужого CSV.

Зачем. Числа 0,600 / 0,528 пришли из прогона, который читал `fails_db.csv`. Файл
собрала другая сессия, и проверить его можно только сверкой с источником. Скрипт
сверяет его с выгрузкой `smvu.fault_episode` построчно и пересчитывает метрики
двумя наборами отказов: всеми эпизодами и только закрытыми длиннее часа —
определением строки М-18.

Выгрузка лежит рядом в `fault_episode_db.csv`, снята 21.09.2026 командой:

    ssh root@135.106.216.101 "docker exec -i moskollektor-db-1 \\
        psql -U moskollektor -d moskollektor --csv" <<'SQL'
    SELECT e.channel_id, e.started_at AT TIME ZONE 'Europe/Moscow' AS t_start,
           (e.ended_at IS NOT NULL AND e.ended_at - e.started_at > interval '1 hour')
             AS closed_long
      FROM smvu.fault_episode e
     WHERE e.fault_value='Неисправен'
       AND e.started_at>='2026-03-20' AND e.started_at<'2026-07-02'
     ORDER BY 2;
    SQL

Время переведено в московское нарочно: база держит время в UTC, а `alerts.csv`
и `fails_db.csv` написаны в московском, и без перевода упреждения уехали бы на 3 часа.

Результат прогона — `recount.log` рядом.
"""
import csv
import os
import sys
from datetime import datetime

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КОРЕНЬ = os.path.abspath(os.path.join(ЗДЕСЬ, "..", "..", "..", ".."))
ДАННЫЕ = os.path.join(os.path.dirname(ЗДЕСЬ), "our-metric")
sys.path.insert(0, os.path.join(КОРЕНЬ, "code"))
from predictive_metrics import (
    collector_key,
    evaluate_alerts,
    group_incidents,
)

время = datetime.fromisoformat


def чит(путь):
    with open(путь, encoding="utf-8") as f:
        return list(csv.DictReader(f))

ch2col, pfx2col = {}, {}
for r in чит(os.path.join(ДАННЫЕ, "ch2collector.csv")):
    if r["collector"]:
        ch2col[int(r["channel_id"])] = int(r["collector"])
        pfx2col.setdefault(r["pfx"], set()).add(int(r["collector"]))

al = [(r["pfx"], время(r["t"])) for r in чит(os.path.join(ДАННЫЕ, "alerts.csv"))]
на_коллекторах = [(f"obj:{c}", t) for p, t in al for c in sorted(pfx2col.get(p, ()))]

эпизоды = list(чит(os.path.join(ЗДЕСЬ, "fault_episode_db.csv")))
из_csv = {(int(r["channel_id"]), время(r["t_start"]))
          for r in чит(os.path.join(ДАННЫЕ, "fails_db.csv"))}
из_базы = {(int(r["channel_id"]), время(r["t_start"])) for r in эпизоды}
print("эпизодов из базы:", len(эпизоды), "| строк fails_db.csv:", len(из_csv))
print("совпало:", len(из_базы & из_csv),
      "| только в базе:", len(из_базы - из_csv),
      "| только в csv:", len(из_csv - из_базы))

# Инциденты по ключу ML-команды — префикс тега. Нужны последней части прогона:
# без них разницу линеек нечем разложить.
инц_pfx = [(r["pfx"], время(r["t_start"]))
           for r in чит(os.path.join(ДАННЫЕ, "incidents_pfx.csv"))]

ОТ, ДО = время("2026-04-01"), время("2026-07-01")
for имя, отбор in (("все эпизоды", lambda r: True),
                   ("закрытые >1ч", lambda r: r["closed_long"] == "t")):
    отказы = [(int(r["channel_id"]), время(r["t_start"])) for r in эпизоды if отбор(r)]
    инциденты = [(k, t) for k, t in group_incidents(отказы, 10, collector_key(ch2col))
                 if ОТ < t < ДО]
    в_окне = [x for x in отказы if ОТ <= x[1] < ДО]
    m24 = evaluate_alerts(на_коллекторах, инциденты, 24, 168)
    m0 = evaluate_alerts(на_коллекторах, инциденты, 0, 168)
    print(f"{имя}: эпизодов в окне {len(в_окне)}, инцидентов {len(инциденты)}, "
          f"P {m24['precision']} R {m24['recall']} "
          f"(TP{m24['tp']}/FP{m24['fp']}/FN{m24['fn']}) | "
          f"медиана {m0['median_lead_hours']} "
          f"доля>=24ч {round(1 - m0['lead_under_24h_share'], 3)}")


# --- MOS-136: метрики при упреждении не меньше суток, четыре линейки ---------------
#
# Строка приёмки М-20а требует «метрики при этом требовании». До 21.09.2026 там
# стояла пара 0,882 / 0,676, посчитанная ML-командой на своём ключе и своём окне,
# а подписана была как наша. Здесь считаются все четыре линейки подряд, каждая
# следующая меняет ровно один параметр — так видно, что даёт требование ≥ 24 ч,
# что смена ключа, а что верхняя граница окна.
отказы = [(int(r["channel_id"]), время(r["t_start"])) for r in эпизоды]
инциденты = [(k, t) for k, t in group_incidents(отказы, 10, collector_key(ch2col))
             if ОТ < t < ДО]
print()
print("MOS-136, метрики при упреждении >= 24 ч:")
for имя, предупр, инц, низ, верх in (
        ("ML-команды: префикс, окно 0…720", al, инц_pfx, 0, 720),
        ("  + требование упреждения >= 24 ч", al, инц_pfx, 24, 720),
        ("  + наш ключ (коллектор)", на_коллекторах, инциденты, 24, 720),
        ("  + наша граница окна (168 ч)", на_коллекторах, инциденты, 24, 168)):
    m = evaluate_alerts(предупр, инц, horizon_hours=низ, max_lead_hours=верх)
    print(f"  {имя:<38} TP {m['tp']:>3} FP {m['fp']:>3} FN {m['fn']:>3}  "
          f"P {m['precision']:.3f} R {m['recall']:.3f}")
