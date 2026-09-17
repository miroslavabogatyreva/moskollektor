#!/usr/bin/env python3
"""Метрики предиктивной аналитики: уровень соответствия правила и оценка предупреждений.

Два расчёта, перенесённые из опыта En+ (СПА для ТЭЦ):

1. rule_compliance — «процент выполнения диагностического правила». Дословно из ТЗ СПА
   v4.0, п.8.3: «каждой метрике диагностического правила должна быть возможность
   назначения весов, при этом сумма весов всех метрик - 100%. Данный инструмент должен
   позволить оценить в текущий момент времени вероятность наступления того или иного
   события, т.е. ещё до его наступления».

2. evaluate_alerts — то, чего в ТЗ En+ не было и из-за чего приёмку нечем было мерить:
   сопоставление предупреждений системы с реальными отказами из журнала в пределах окна
   упреждения. Даёт precision / recall / медиану упреждения / частоту ложных тревог.
   Это методика проверки целевых метрик Москоллектора: Precision > 0.7, Recall > 0.5,
   горизонт >= 24 ч.

Только стандартная библиотека. Самопроверка: python3 predictive_metrics.py
"""

from datetime import datetime, timedelta
from statistics import median

# ---------------------------------------------------------------- правило


def rule_compliance(weights, fired):
    """Уровень соответствия диагностического правила, %.

    weights: {имя метрики: вес}, сумма весов должна быть ровно 100.
    fired:   множество/список имён метрик, сработавших сейчас.

    Возвращает (процент, список сработавших, список несработавших).
    """
    total = sum(weights.values())
    if round(total, 6) != 100:
        raise ValueError(f"сумма весов метрик должна быть 100, получено {total}")
    unknown = set(fired) - set(weights)
    if unknown:
        raise ValueError(f"метрики вне правила: {sorted(unknown)}")

    hit = [m for m in weights if m in set(fired)]
    miss = [m for m in weights if m not in set(fired)]
    return sum(weights[m] for m in hit), hit, miss


# ------------------------------------------------------- склейка в инциденты


def group_incidents(failures, window_minutes=10, group_key=None):
    """Склеивает отказы, случившиеся рядом по времени в одной группе, в один инцидент.

    Зачем. В журнале СМВУ отказы приходят пачками: 04.06.2026 в 09:22:32 значение
    «Неисправен» записалось у 75 каналов сразу, на трёх префиксах. Семьдесят пять
    датчиков разных типов не ломаются в одну секунду — упал шлейф или контроллер,
    а журнал записал событие по каждому каналу. Если считать это 75 отказами, модель,
    предсказавшая один такой случай, получит 75 попаданий из 587 — 13 % Recall
    за одно верное предсказание.

    failures:  [(object_id, datetime отказа), ...]
    window_minutes: отказы в одной группе, отстоящие меньше чем на это время,
        считаются одним инцидентом. Цепочка склеивается по соседям: если между
        соседними отказами меньше окна, они в одном инциденте, даже когда первый
        и последний разошлись сильнее.
    group_key: функция object_id -> ключ группы. Для нас группа — коллектор, и берётся
        он из дерева объектов заказчика, а не из префикса тега: см. collector_key()
        ниже. None означает «склеивать только внутри одного объекта».

    Возвращает [(ключ группы, время первого отказа инцидента), ...] — тот же формат,
    что принимает evaluate_alerts, поэтому результат подаётся ей напрямую.
    """
    key = group_key or (lambda obj: obj)
    window = timedelta(minutes=window_minutes)

    out = []
    for grp, t in sorted(((key(obj), t) for obj, t in failures)):
        if out and out[-1][0] == grp and t - prev <= window:
            prev = t          # тот же инцидент, время начала не трогаем
            continue
        out.append((grp, t))
        prev = t
    return out


def collector_key(коллектор_канала):
    """Ключ группы для group_incidents: коллектор из дерева объектов заказчика.

    **Почему не префикс тега, как было до 17.09.2026.** Префикс — это начало тега
    до дефиса, и мы принимали его за коллектор. Справочник каналов от 15.09.2026
    это опроверг: префиксов 32, а коллекторов в дереве 16, у 11 коллекторов
    по нескольку префиксов, и два префикса ведут к РАЗНЫМ коллекторам — 798 отдаёт
    139 каналов объекту Зита и 25 каналов объекту Бета, 163 отдаёт 44 канала
    ПС объекта Ро и 1 канал объекту Каппа. Заказчик 17.09.2026 сказал про тег
    прямо: «его можно игнорировать, он избыточный. Просто смотрите новый файл,
    где ИД объект».

    коллектор_канала: словарь «канал -> узел коллектора», узел второго уровня
        smvu.object_tree. Собирается запросом, а не разбором строки.

    **У канала без узла ключ СВОЙ, а не общий.** Это главное место, где легко
    получить тихий ноль: один общий ключ на все каналы без узла склеил бы их
    отказы в один инцидент, и счётчик упал бы без единой ошибки. В выгрузке
    от 15.09.2026 без узла 1 142 канала из 12 627, и все 1 142 — заглушки
    (`smvu.channel.is_stub`).

    **Ветка эта не мёртвая, хотя на проверочном окне не срабатывает ни разу.**
    В окне апрель–июнь 2026 эпизодов у каналов без узла ноль, а по всему журналу
    их 283 из 10 419 закрытых эпизодов «Неисправен» длиннее часа, плюс ещё 6
    из 57 незакрытых. Сдвинется окно — придут все 283. Проверено 17.09.2026
    на стенде; расхождение с числом 289, которое даёт тот же запрос без условия
    `ended_at IS NOT NULL`, — это ровно те 57 незакрытых эпизодов.
    """
    def ключ(канал):
        узел = коллектор_канала.get(канал)
        return f"ch:{канал}" if узел is None else f"obj:{узел}"
    return ключ


def precision_recall(tp, fp, fn):
    """Precision и recall из сырых счётчиков, без округления.

    Единственное место с этой формулой (MOS-116): её звали трижды —
    evaluate_alerts() для отчётных округлённых полей, verdict() для приёмочного
    решения и code/check_metrics.py для строк М-18/М-19 — и три копии могли
    разойтись молча, как уже было с числом инцидентов (166 вместо 176).
    """
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


# ------------------------------------------------------------ оценка алертов


def evaluate_alerts(alerts, failures, horizon_hours=24, max_lead_hours=None,
                    observed_object_days=None):
    """Сопоставляет предупреждения с фактическими отказами.

    alerts:   [(object_id, datetime выдачи предупреждения), ...]
    failures: [(object_id, datetime фактического отказа), ...]
    horizon_hours: минимальное упреждение. Предупреждение засчитывается,
        только если выдано НЕ ПОЗЖЕ чем за horizon_hours до отказа — иначе
        ремонтники не успевают, и польза нулевая.
    max_lead_hours: верхняя граница окна. Предупреждение за полгода до отказа
        связано с ним случайно. None — без верхней границы.
    observed_object_days: сколько объекто-суток наблюдали (для частоты ложных тревог).

    Один отказ закрывается одним предупреждением (самым поздним из подходящих —
    оно точнее по времени). Остальные предупреждения по тому же объекту в том же
    окне считаются дублями, а не отдельными ложными: диспетчер видит один инцидент.
    """
    lo = timedelta(hours=horizon_hours)
    hi = timedelta(hours=max_lead_hours) if max_lead_hours else None

    alerts = sorted(alerts, key=lambda a: a[1])
    used = [False] * len(alerts)
    matched_to_failure = [False] * len(alerts)  # включая дубли в окне
    tp, fn, leads = 0, 0, []

    for obj, t_fail in sorted(failures, key=lambda f: f[1]):
        best = None
        for i, (a_obj, t_alert) in enumerate(alerts):
            if a_obj != obj or t_alert > t_fail:
                continue
            lead = t_fail - t_alert
            if lead < lo or (hi and lead > hi):
                continue
            matched_to_failure[i] = True
            if not used[i] and (best is None or t_alert > alerts[best][1]):
                best = i
        if best is None:
            fn += 1
        else:
            used[best] = True
            tp += 1
            leads.append((t_fail - alerts[best][1]).total_seconds() / 3600)

    fp = sum(1 for i in range(len(alerts)) if not matched_to_failure[i])

    precision, recall = precision_recall(tp, fp, fn)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    out = {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "median_lead_hours": round(median(leads), 1) if leads else None,
        "min_lead_hours": round(min(leads), 1) if leads else None,
        # Доля пойманных отказов, о которых предупредили позже чем за 24 часа.
        # Смысл имеет только при horizon_hours = 0: при ненулевом горизонте отбор
        # совпадений уже выбросил такие предупреждения, и доля выйдет нулевой
        # не потому, что их нет, а потому, что их не пустили в выборку.
        "lead_under_24h_share": (
            round(sum(1 for x in leads if x < 24) / len(leads), 3) if leads else None),
        "horizon_hours": horizon_hours,   # нужен verdict(), см. ниже
    }
    if observed_object_days:
        out["false_alarms_per_1000_object_days"] = round(
            fp / observed_object_days * 1000, 2)
    return out


def verdict(metrics, min_precision=0.7, min_recall=0.5, min_lead_hours=24,
            max_under_share=0.20):
    """Приёмочное решение по целевым метрикам Москоллектора.

    Про lead_time. evaluate_alerts уже выбросила все предупреждения с
    упреждением меньше horizon_hours, поэтому при horizon_hours >= min_lead_hours
    проверка упреждения не может провалиться — она ничего не измеряет.
    В этом случае возвращаем None вместо True, чтобы вакуумная проверка
    не выглядела на приёмке как пройденная. Чтобы реально померить упреждение,
    прогоните evaluate_alerts(..., horizon_hours=0) и смотрите распределение.

    Что именно проверяем при честном прогоне. Раньше здесь стоял МИНИМУМ упреждения:
    «ни один прогноз не выдан позже чем за 24 часа». Такой порог не берёт никто —
    достаточно одного позднего прогноза из тысячи, чтобы вся приёмка провалилась,
    и пример в docs/acceptance-test.md это показывал: медиана 72 часа при минимуме 6.
    Поэтому проверяем два числа вместе, как записано в строке М-20:
    медиана не ниже 24 часов И доля поздних прогнозов не выше max_under_share.
    Минимум по-прежнему считается и выводится, но приёмку в одиночку не рушит.
    """
    horizon = metrics.get("horizon_hours", 0)
    if horizon >= min_lead_hours:
        lead_ok = None          # не измерено: отбор совпадений уже гарантировал порог
    else:
        median_ok = (metrics.get("median_lead_hours") or 0) >= min_lead_hours
        share = metrics.get("lead_under_24h_share")
        share_ok = share is not None and share <= max_under_share
        lead_ok = median_ok and share_ok
    # Precision и recall считаем заново из tp/fp/fn через precision_recall(),
    # а не берём округлённые metrics["precision"]/["recall"]: evaluate_alerts
    # округляет их до трёх знаков, и настоящие 0,7004 приехали бы как 0,700
    # и провалили бы строгое «больше 0,7» на ровном месте. Сравнение строгое:
    # ровно 0,700 постановку не закрывает.
    tp, fp, fn = metrics["tp"], metrics["fp"], metrics["fn"]
    precision, recall = precision_recall(tp, fp, fn)
    checks = {
        "precision": precision > min_precision,
        "recall": recall > min_recall,
        "lead_time": lead_ok,
    }
    passed = all(v for v in checks.values() if v is not None)
    return passed, checks


# ---------------------------------------------------------------- самопроверка

def _demo():
    # Пример из md: правило «неисправность подшипникового узла».
    w = {"рост вибрации": 50, "рост температуры": 30, "падение расхода масла": 20}
    pct, hit, miss = rule_compliance(w, ["рост вибрации", "рост температуры"])
    assert pct == 80, pct
    assert miss == ["падение расхода масла"]
    assert rule_compliance(w, [])[0] == 0
    assert rule_compliance(w, list(w))[0] == 100

    try:
        rule_compliance({"a": 60, "b": 30}, ["a"])
        raise AssertionError("должно было упасть: сумма весов 90")
    except ValueError:
        pass

    d = lambda s: datetime.fromisoformat(s)

    # Склейка в инциденты. Случай из выгрузки: 04.06.2026 09:22:32 «Неисправен»
    # записался у 75 каналов сразу. Считать это 75 отказами нельзя — упал один шлейф.
    burst = [(f"257-{i}", d("2026-06-04 09:22:32")) for i in range(75)]
    pref = lambda obj: obj.split("-")[0]
    assert group_incidents(burst, group_key=pref) == [("257", d("2026-06-04 09:22:32"))]

    # Разные коллекторы в одну секунду — разные инциденты, склеивать их нечем.
    two = [("257-1", d("2026-06-04 09:22:32")), ("884-1", d("2026-06-04 09:22:32"))]
    assert len(group_incidents(two, group_key=pref)) == 2

    # Цепочка: по 6 минут между соседями при окне 10 — это один инцидент,
    # хотя от первого до последнего 18 минут. Склейка идёт по соседям, а не от начала.
    chain = [("257-a", d("2026-06-04 09:00")), ("257-b", d("2026-06-04 09:06")),
             ("257-c", d("2026-06-04 09:12")), ("257-d", d("2026-06-04 09:18"))]
    assert group_incidents(chain, group_key=pref) == [("257", d("2026-06-04 09:00"))]

    # Разрыв больше окна — второй инцидент, и время у него своё.
    gap = [("257-a", d("2026-06-04 09:00")), ("257-b", d("2026-06-04 09:30"))]
    assert group_incidents(gap, group_key=pref) == [
        ("257", d("2026-06-04 09:00")), ("257", d("2026-06-04 09:30"))]

    # Без group_key склейка идёт внутри объекта: два канала не сливаются.
    assert len(group_incidents(two)) == 2

    # --- Ключ коллектора: дерево объектов, а не префикс тега (MOS-103) ---------
    # Случай из справочника заказчика: префикс 798 ведёт к ДВУМ коллекторам.
    # На этих четырёх строках старый ключ обязан дать неверный ответ, а новый —
    # верный, иначе проверка ничего не проверяет.
    дерево = {"798-1": 12, "798-2": 12,   # объект Зита
              "798-9": 6,  "798-8": 6}    # объект Бета
    рядом = [("798-1", d("2026-06-04 09:22:00")), ("798-9", d("2026-06-04 09:22:30")),
             ("798-2", d("2026-06-04 09:24:00")), ("798-8", d("2026-06-04 09:24:30"))]
    # Старый ключ: все четыре канала «один коллектор 798» — один инцидент. Это ложь:
    # упали два разных коллектора, диспетчер получил два события.
    assert len(group_incidents(рядом, group_key=pref)) == 1
    # Новый ключ: два инцидента, по одному на коллектор.
    ключ = collector_key(дерево)
    assert len(group_incidents(рядом, group_key=ключ)) == 2, \
        "ключ по дереву обязан развести два коллекторa одного префикса"

    # Обратный случай: разные префиксы, один коллектор. Старый ключ насчитает два
    # инцидента там, где авария одна. У объекта Мю так и есть: префиксы 914 и 915.
    один = [("914-1", d("2026-04-18 09:00:00")), ("915-7", d("2026-04-18 09:03:00"))]
    assert len(group_incidents(один, group_key=pref)) == 2
    assert len(group_incidents(один, group_key=collector_key({"914-1": 3828, "915-7": 3828}))) == 1

    # Канал без узла в дереве получает СВОЙ ключ, а не общий: иначе все 1 142
    # канала-заглушки склеились бы в один инцидент.
    безузла = [("ch1", d("2026-06-04 09:00")), ("ch2", d("2026-06-04 09:01"))]
    assert len(group_incidents(безузла, group_key=collector_key({}))) == 2

    alerts = [
        ("K-101", d("2026-01-01 08:00")),   # TP: за 72 ч до отказа
        ("K-101", d("2026-01-02 08:00")),   # дубль в том же окне, не FP
        ("K-205", d("2026-02-10 12:00")),   # FP: отказа не было
        ("K-307", d("2026-03-01 23:00")),   # слишком поздно (2 ч) -> не спасает
    ]
    failures = [
        ("K-101", d("2026-01-04 08:00")),
        ("K-307", d("2026-03-02 01:00")),   # FN: предупредили за 2 ч
        ("K-409", d("2026-03-15 06:00")),   # FN: не предупредили вовсе
    ]

    m = evaluate_alerts(alerts, failures, horizon_hours=24,
                        observed_object_days=2000)
    assert m["tp"] == 1, m
    assert m["fn"] == 2, m
    # K-307 выдан позже порога и ни к чему не привязан -> FP; K-205 -> FP
    assert m["fp"] == 2, m
    assert m["precision"] == round(1 / 3, 3), m
    assert m["recall"] == round(1 / 3, 3), m
    assert m["median_lead_hours"] == 48.0, m  # берём самое позднее подходящее
    assert m["false_alarms_per_1000_object_days"] == 1.0, m

    ok, checks = verdict(m)
    assert ok is False and checks["precision"] is False

    # Строгое неравенство и ловушка округления (MOS-105): verdict не смотрит
    # в округлённое metrics["precision"], а считает из tp/fp/fn заново.
    # 7000/10000 в double совпадает с литералом 0.7 — граница ровная, без эпсилон.
    assert 7000 / 10000 == 0.7
    ровно_07 = {"tp": 7000, "fp": 3000, "fn": 3000, "horizon_hours": 24}
    assert verdict(ровно_07)[1]["precision"] is False, verdict(ровно_07)
    чуть_выше = {"tp": 7001, "fp": 2999, "fn": 3000, "horizon_hours": 24}
    assert verdict(чуть_выше)[1]["precision"] is True, verdict(чуть_выше)

    # Сама ловушка округления: 7004/10000 — это 0,7004, постановку закрывает,
    # но evaluate_alerts кладёт в metrics["precision"] округлённые 0,7, а строгое
    # «больше 0,7» на них отвечает «нет». Кладу округлённое поле в словарь нарочно:
    # если verdict когда-нибудь снова начнёт читать его вместо tp/fp, этот assert
    # покажет False и рабочая модель провалит М-18 на пустом месте.
    округление = {"tp": 7004, "fp": 2996, "fn": 3000, "horizon_hours": 24,
                  "precision": round(7004 / 10000, 3), "recall": 0.7}
    assert округление["precision"] == 0.7, округление
    assert verdict(округление)[1]["precision"] is True, verdict(округление)

    good = evaluate_alerts(
        [("A", d("2026-01-01 00:00")), ("B", d("2026-01-01 00:00"))],
        [("A", d("2026-01-03 00:00")), ("B", d("2026-01-02 12:00"))],
        horizon_hours=24, observed_object_days=500)
    assert good["precision"] == 1.0 and good["recall"] == 1.0, good
    assert verdict(good)[0] is True

    # Честный прогон: horizon_hours=0 ничего не отбрасывает, и упреждение измеряется
    # по-настоящему. Пять отказов, один из них предупреждён поздно — это доля 0,20,
    # ровно на границе допустимого по М-20.
    late_alerts, late_failures = [], []
    for i, lead_h in enumerate([72, 48, 36, 30, 6]):
        obj = f"S-{i}"
        fail_at = d("2026-04-10 00:00")
        late_alerts.append((obj, fail_at - timedelta(hours=lead_h)))
        late_failures.append((obj, fail_at))
    honest = evaluate_alerts(late_alerts, late_failures, horizon_hours=0)
    assert honest["median_lead_hours"] == 36.0, honest
    assert honest["min_lead_hours"] == 6.0, honest          # минимум порог не берёт
    assert honest["lead_under_24h_share"] == 0.2, honest    # один поздний из пяти
    ok_lead, checks_lead = verdict(honest, min_precision=0, min_recall=0)
    assert checks_lead["lead_time"] is True, checks_lead    # медиана 36 ч, доля 0,20
    assert ok_lead is True, checks_lead

    # Шестой отказ, тоже предупреждённый поздно: доля 2 из 6 = 0,33 — порог пробит,
    # хотя медиана всё ещё выше 24 часов. Именно это и обязана поймать проверка.
    late_alerts.append(("S-5", d("2026-04-10 00:00") - timedelta(hours=10)))
    late_failures.append(("S-5", d("2026-04-10 00:00")))
    worse = evaluate_alerts(late_alerts, late_failures, horizon_hours=0)
    assert worse["median_lead_hours"] == 33.0, worse
    assert worse["lead_under_24h_share"] == 0.333, worse
    assert verdict(worse, min_precision=0, min_recall=0)[1]["lead_time"] is False, worse

    print("rule_compliance: 50+30 из 100 ->", pct, "% | сработали:", hit)
    print("evaluate_alerts:", m)
    print("verdict:", verdict(m))
    print("OK")


if __name__ == "__main__":
    _demo()
