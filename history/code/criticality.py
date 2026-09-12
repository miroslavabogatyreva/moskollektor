#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Считалка критичности объекта по методике взвешенных критериев.

Основа — «Методологические рекомендации по ранжированию инвестиционных проектов
в ОАО "Газпром"» (ИУС И, 2011). Оттуда взято три вещи:
  1) все критерии приводятся к общей шкале 1..4 (нормирование),
  2) итог — средневзвешенная сумма P = sum(k_i * w_i), sum(w_i) = 1,
  3) P раскладывается по категориям: 3..4 высокая, 2..3 средняя, 1..2 низкая,
     плюс отдельная «нулевая» категория для объектов с признаком
     «обязательный к реализации» (у нас — «обязательный к обслуживанию»).

Здесь две модели:
  GAZPROM_ECONOMIC / GAZPROM_NONECONOMIC — веса из Приложения 2 методики,
     нужны для самопроверки: они воспроизводят пример из Приложения 3 (P = 2,06).
  MOSKOLLEKTOR — мой набор факторов для коллекторов и оборудования Москоллектора.
     Веса подобраны мной, а не взяты из документа; их надо калибровать на истории
     отказов, как и советует сама методика («веса могут настраиваться по
     результатам ранжирования нескольких периодов»).

Запуск: python criticality.py  — прогоняет самопроверку.
"""

# --- Модель Газпрома, Приложение 2 методики ---------------------------------
# Таблица 8.1, «Экономические» проекты
GAZPROM_ECONOMIC = {
    "npvr_norm": 0.50,          # нормированный относительный ЧДД
    "political": 0.10,          # политическая значимость
    "operational": 0.10,        # операционная необходимость
    "gas_balance": 0.10,        # обеспечение выполнения баланса газа
    "stage": 0.10,              # стадия реализации
    "docs": 0.10,               # наличие утверждённой проектной документации
}
# Таблица 8.1, «Неэкономические» проекты
GAZPROM_NONECONOMIC = {
    "political": 0.25,
    "operational": 0.40,
    "stage": 0.15,
    "docs": 0.20,
}

# --- Моя модель для Москоллектора -------------------------------------------
# Вероятность отказа (POF) — «как вероятно, что рванёт»
MK_POF = {
    "age": 0.25,            # возраст относительно нормативного срока службы
    "failure_history": 0.30,  # число отказов/сработок за 3 года
    "condition": 0.20,      # результат последнего осмотра/диагностики
    "environment": 0.15,    # подтопление, агрессивная среда, глубина заложения
    "sensor_health": 0.10,  # доля «плохих» показаний СМВУ за 90 дней
}
# Последствие отказа (COF) — «сколько будет стоить, если рванёт»
MK_COF = {
    "safety": 0.30,         # угроза жизни людей (персонал, население)
    "cables": 0.25,         # сколько кабелей и какого класса рядом
    "downtime": 0.20,       # число отключаемых потребителей × время восстановления
    "money": 0.15,          # прямые затраты на восстановление
    "reputation": 0.10,     # публичность: центр, метро, объекты города
}

SCALE_MIN, SCALE_MAX = 1.0, 4.0


def score(factors, weights):
    """Средневзвешенная оценка по шкале 1..4.

    factors  — {имя фактора: значение 1..4}
    weights  — {имя фактора: вес}, сумма весов = 1

    Отсутствующий фактор — ошибка, а не молчаливый ноль: критичность, посчитанная
    по половине факторов, выглядит как настоящая и врёт.
    """
    total_w = sum(weights.values())
    if abs(total_w - 1.0) > 1e-9:
        raise ValueError("сумма весов должна быть 1, получено %.4f" % total_w)
    missing = set(weights) - set(factors)
    if missing:
        raise ValueError("нет значений для факторов: %s" % ", ".join(sorted(missing)))
    for name in weights:
        v = factors[name]
        if not (SCALE_MIN <= v <= SCALE_MAX):
            raise ValueError("фактор %s = %r вне шкалы 1..4" % (name, v))
    return sum(factors[n] * w for n, w in weights.items())


def category(p, mandatory=False):
    """Категория приоритета по методике (раздел 4.2.2).

    I   — «обязательный к реализации», независимо от P
    II  — высокий приоритет, P от 3 до 4
    III — средний, P от 2 до 3
    IV  — низкий, P от 1 до 2
    """
    if mandatory:
        return "I"
    if p > 3.0:
        return "II"
    if p > 2.0:
        return "III"
    return "IV"


def normalize_npvr(npvr):
    """Нормирование относительного ЧДД к шкале 1..4.

    Формула в .doc осталась картинкой (EMBED Equation.3), но все три примера
    Приложения 3 дают ровно NPVR + 1: 0,12 -> 1,12; 0,26 -> 1,26; 0,28 -> 1,28.
    Потолок 4 задан в самой методике текстом.
    """
    return min(npvr + 1.0, SCALE_MAX)


def portfolio(projects, weights, key="pv_invest"):
    """Оценка портфеля: средневзвешенное по объёму инвестиций (раздел 4.2.1).

    projects — список {"pv_invest": число, "factors": {...}}
    Возвращает (значения факторов портфеля, интегральная оценка портфеля).
    """
    total = sum(p[key] for p in projects)
    agg = {}
    for name in weights:
        agg[name] = sum(p["factors"][name] * p[key] / total for p in projects)
    return agg, score(agg, weights)


def risk_rank(pof_factors, cof_factors, pof_weights=MK_POF, cof_weights=MK_COF):
    """Критичность объекта Москоллектора: вероятность x последствие.

    Возвращает словарь: POF и COF по шкале 1..4, произведение (1..16),
    ранг 1..4 и зона матрицы риска.
    """
    pof = score(pof_factors, pof_weights)
    cof = score(cof_factors, cof_weights)
    r = pof * cof
    # Границы зон 4 / 8 / 12 делят диапазон 1..16 на четыре равные части.
    # ponytail: равномерное деление; после года эксплуатации границы надо
    # подвинуть так, чтобы в красную зону попадало ~5% фонда, а не 30%.
    if r > 12:
        zone, rank = "красная", 4
    elif r > 8:
        zone, rank = "оранжевая", 3
    elif r > 4:
        zone, rank = "жёлтая", 2
    else:
        zone, rank = "зелёная", 1
    return {"pof": pof, "cof": cof, "risk": r, "rank": rank, "zone": zone}


def inspection_interval(risk, base_months=36, min_months=1, max_months=60):
    """Интервал обслуживания из критичности: T = T_base / risk.

    Логика RBI: чем выше риск, тем чаще смотрим. При risk=1 (зелёная зона)
    интервал равен базовому нормативу, при risk=16 — в 16 раз короче.
    Обрезаем снизу и сверху, чтобы не получить «раз в 3 дня» или «раз в 10 лет».
    """
    months = base_months / float(risk)
    return max(min_months, min(max_months, round(months, 1)))


def demo():
    # 1. Пример из Приложения 3 методики, Проект 1.
    #    NPVR 0,12 -> 1,12; стадия 3; политика 3; операционная 1; баланс газа 4;
    #    документация 4. Ожидаемый ответ методики: P = 2,06.
    p1 = {
        "npvr_norm": normalize_npvr(0.12),
        "political": 3, "operational": 1, "gas_balance": 4,
        "stage": 3, "docs": 4,
    }
    assert abs(normalize_npvr(0.12) - 1.12) < 1e-9
    p = score(p1, GAZPROM_ECONOMIC)
    assert abs(p - 2.06) < 0.005, p
    assert category(p) == "III"
    print("Проект 1 (Приложение 3): P = %.2f, категория %s" % (p, category(p)))

    # 2. Портфель из трёх проектов, тот же Приложение 3.
    #    Веса — доли приведённых инвестиций: 154,77 / 216,75 / 181,22 = 552,74.
    #    Ожидаемый ответ методики: NPVRnorm 1,23; стадия 2,84; документация 3,01; P = 2,00.
    pr = [
        {"pv_invest": 154.77, "factors": dict(p1)},
        {"pv_invest": 216.75, "factors": {
            "npvr_norm": normalize_npvr(0.26), "political": 3, "operational": 1,
            "gas_balance": 4, "stage": 3, "docs": 4}},
        {"pv_invest": 181.22, "factors": {
            "npvr_norm": normalize_npvr(0.28), "political": 3, "operational": 1,
            "gas_balance": 4, "stage": 2.5, "docs": 1}},
    ]
    agg, pp = portfolio(pr, GAZPROM_ECONOMIC)
    assert abs(agg["npvr_norm"] - 1.23) < 0.005, agg
    assert abs(agg["stage"] - 2.84) < 0.005, agg
    assert abs(agg["docs"] - 3.01) < 0.01, agg   # в документе округлено до 3,01
    assert abs(pp - 2.00) < 0.005, pp
    print("Портфель 1-3: NPVRnorm %.2f, стадия %.2f, документация %.2f, P = %.2f"
          % (agg["npvr_norm"], agg["stage"], agg["docs"], pp))

    # 3. Пример со слайда 19 презентации «1-37 ранжирование»:
    #    50%*3,5 + 12,5%*(3 + 4 + 1 + 1,5) = 2,9.
    old_weights = {"fin": 0.5, "strategy": 0.125, "political": 0.125,
                   "operational": 0.125, "stage": 0.125}
    slide = {"fin": 3.5, "strategy": 3, "political": 4, "operational": 1, "stage": 1.5}
    assert abs(score(slide, old_weights) - 2.94) < 0.005
    print("Слайд 19: P = %.2f" % score(slide, old_weights))

    # 4. Объект Москоллектора: коллектор 1968 года в центре, три отказа за 3 года,
    #    подтопление, рядом силовые кабели 110 кВ.
    pof = {"age": 4, "failure_history": 4, "condition": 3,
           "environment": 4, "sensor_health": 2}
    cof = {"safety": 4, "cables": 4, "downtime": 3, "money": 3, "reputation": 4}
    r = risk_rank(pof, cof)
    assert abs(r["pof"] - 3.60) < 0.005, r
    assert abs(r["cof"] - 3.65) < 0.005, r
    assert r["zone"] == "красная", r
    print("Коллектор ЦАО-1968: POF %.2f x COF %.2f = %.2f, ранг %d (%s), "
          "осмотр раз в %.1f мес."
          % (r["pof"], r["cof"], r["risk"], r["rank"], r["zone"],
             inspection_interval(r["risk"])))

    # 5. Контрпример: новая вентшахта на окраине без отказов.
    pof2 = {"age": 1, "failure_history": 1, "condition": 1,
            "environment": 2, "sensor_health": 1}
    cof2 = {"safety": 2, "cables": 1, "downtime": 1, "money": 1, "reputation": 1}
    r2 = risk_rank(pof2, cof2)
    assert r2["zone"] == "зелёная", r2
    print("Вентшахта ТиНАО-2019: POF %.2f x COF %.2f = %.2f, ранг %d (%s), "
          "осмотр раз в %.1f мес."
          % (r2["pof"], r2["cof"], r2["risk"], r2["rank"], r2["zone"],
             inspection_interval(r2["risk"])))

    # 6. Защита от дырок во входных данных.
    try:
        score({"age": 4}, MK_POF)
    except ValueError as e:
        print("Проверка на неполные данные сработала: %s" % e)
    else:
        raise AssertionError("неполный набор факторов должен падать")

    print("Все проверки прошли.")


if __name__ == "__main__":
    demo()
