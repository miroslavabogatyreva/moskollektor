#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Четыре правила выявления температурной аномалии.

Логику я взял из ТЗ «Тепловизионный мониторинг ОРУ ИГЭС» (2020, ООО «РУСАЛ ИТЦ»
для ЕвроСибЭнерго-Гидрогенерация), раздел 5.2 «Описание работы СПТМ». Там система
работает по двум алгоритмам: сначала «сигнализация» (оператор руками вбивает
предельную температуру), потом «интеллектуальный» — три проверки на исторических
данных. Я разложил это на четыре независимых правила:

    R1 ABSOLUTE   — превышение уставки, которую задал оператор.
    R2 CONDITION  — температура вышла из коридора, характерного для этого
                    элемента при текущих условиях (уличная температура, нагрузка,
                    сезон, осадки). Цитата из ТЗ: «Проверять, укладывается ли
                    температура элемента в диапазон характерный для этого элемента
                    при существующих условиях».
    R3 PEER       — статический контроль: элемент горячее соседей того же типа,
                    работающих в тех же условиях. В энергетике это классическое
                    «сравнение с соседней фазой».
    R4 TREND      — динамический контроль: скорость роста температуры выше, чем
                    у соседей того же типа и чем у самого элемента в прошлые
                    периоды при таких же условиях.

Каждое правило возвращает либо None (тихо), либо Finding с уровнем и объяснением
в человеческих словах — чтобы диспетчер видел не «аномалия 0.83», а «шина 110-4В
на 8.5 °C горячее двух других фаз».

Пороги здесь — дефолтные, их надо калибровать на реальных данных: у коллектора
другая физика, чем у ОРУ. Все они вынесены в Thresholds, поменять можно на месте.

Запуск: python3 thermal_anomaly_rules.py
"""

from collections import namedtuple
from statistics import mean, median, pstdev

# level: "warn" — предупреждение, "alarm" — тревога.
Finding = namedtuple("Finding", "rule level value explain")

Thresholds = namedtuple(
    "Thresholds",
    "abs_alarm abs_warn band_sigma peer_warn peer_alarm trend_ratio trend_min_rate",
)

DEFAULT = Thresholds(
    abs_alarm=None,      # уставку задаёт оператор на объект, дефолта нет
    abs_warn=0.9,        # предупреждение при 90 % от уставки
    band_sigma=3.0,      # выход за 3 сигмы коридора «норма при этих условиях»
    peer_warn=5.0,       # +5 °C к медиане соседей того же типа — предупреждение
    peer_alarm=10.0,     # +10 °C — тревога
    trend_ratio=2.0,     # скорость роста вдвое выше соседской
    trend_min_rate=0.2,  # но не реагируем на рост медленнее 0.2 °C/ч
)


def r1_absolute(temp, setpoint, th=DEFAULT, setpoint_low=None):
    """R1: выход за уставку. Уставку вводит оператор на объект или на зону.

    Уставок две, а не одна. В журнале заказчика за 2025 год есть обе:
    «Температура ниже 3ºC» — 1 640 записей на 199 каналах, «Температура выше 40ºC» —
    367 записей на 35 каналах. Нижняя уставка для коллектора не экзотика: остывший
    ниже нуля участок это риск разморозки трубопровода, и прибор заказчика тревогу
    по нему поднимает. Пока правило умело только верхнюю, вызов r1_absolute(2.0, 3.0)
    возвращал None — камера остыла до +2 при уставке +3, а мы молчали.

    ОСТОРОЖНО СО ЗНАКОМ ГРАДУСА, если будете искать эти строки в данных регуляркой.
    Заказчик пишет не «°C» (U+00B0, знак градуса), а «ºC» (U+00BA, порядковый
    индикатор). Проверено на выгрузке: во всех строках стоит 0xBA. Регулярное
    выражение на «°C» не найдёт ни одной записи.
    """
    if setpoint_low is not None and temp <= setpoint_low:
        return Finding(
            "R1_ABSOLUTE", "alarm", temp,
            "температура %.1f ºC опустилась до нижней уставки %.1f ºC"
            % (temp, setpoint_low),
        )
    if setpoint is None:
        return None
    if temp >= setpoint:
        return Finding(
            "R1_ABSOLUTE", "alarm", temp,
            "температура %.1f °C достигла уставки %.1f °C" % (temp, setpoint),
        )
    if temp >= setpoint * th.abs_warn:
        return Finding(
            "R1_ABSOLUTE", "warn", temp,
            "температура %.1f °C — это %.0f %% от уставки %.1f °C"
            % (temp, 100 * temp / setpoint, setpoint),
        )
    return None


def r2_condition_band(temp, history_same_conditions, th=DEFAULT):
    """R2: вышли из коридора «норма при этих условиях».

    history_same_conditions — значения этого же датчика за прошлые периоды,
    отобранные по похожим условиям (уличная температура, нагрузка, сезон).
    Коридор считаю как среднее ± band_sigma * сигма. Меньше 10 точек — молчу,
    статистики нет.
    """
    if len(history_same_conditions) < 10:
        return None
    mu = mean(history_same_conditions)
    sd = pstdev(history_same_conditions)
    if sd == 0:
        return None
    z = (temp - mu) / sd
    if abs(z) < th.band_sigma:
        return None
    level = "alarm" if abs(z) >= th.band_sigma + 1 else "warn"
    return Finding(
        "R2_CONDITION", level, z,
        "при таких же условиях этот элемент обычно даёт %.1f ± %.1f °C, "
        "сейчас %.1f °C — это %.1f сигмы" % (mu, sd, temp, z),
    )


def r3_peer(temp, peers, th=DEFAULT):
    """R3: статический контроль — сравнение с соседями того же типа.

    peers — температуры однотипных элементов в тех же условиях (соседние фазы,
    соседние датчики в той же камере коллектора). Сравниваю с медианой, а не со
    средним: одна перегретая фаза не должна тянуть базу вверх.

    Медиану берём настоящую. Раньше здесь стояло sorted(peers)[len(peers) // 2],
    и на чётном списке это верхнее из двух средних, а не медиана. Разница молчаливая
    и всегда в одну сторону — база завышается, предупреждение не выдаётся:
    r3_peer(50.0, [40.0, 50.0]) возвращал None при базе 50, хотя настоящая медиана 45
    даёт превышение на 5 градусов.
    """
    if len(peers) < 2:
        return None
    base = median(peers)
    delta = temp - base
    if delta >= th.peer_alarm:
        level = "alarm"
    elif delta >= th.peer_warn:
        level = "warn"
    else:
        return None
    return Finding(
        "R3_PEER", level, delta,
        "на %.1f °C горячее однотипных соседей (%s °C, медиана %.1f)"
        % (delta, ", ".join("%.1f" % p for p in peers), base),
    )


def r4_trend(rate, peer_rates, own_past_rates=(), th=DEFAULT):
    """R4: динамический контроль — аномалия тренда.

    rate — скорость изменения температуры элемента, °C/ч. peer_rates — то же у
    однотипных соседей, own_past_rates — скорости у самого элемента в прошлые
    периоды при похожих условиях. Реагирую, когда рост и сам по себе заметный
    (>= trend_min_rate), и в trend_ratio раз обгоняет базу сравнения.
    """
    if rate < th.trend_min_rate:
        return None
    bases = []
    if peer_rates:
        bases.append(("соседей", mean(peer_rates)))
    if own_past_rates:
        bases.append(("себя в прошлом", mean(own_past_rates)))
    for what, base in bases:
        if base <= 0:
            continue
        if rate >= base * th.trend_ratio:
            return Finding(
                "R4_TREND", "warn", rate,
                "растёт на %.2f °C/ч, у %s %.2f °C/ч — быстрее в %.1f раза"
                % (rate, what, base, rate / base),
            )
    return None


def evaluate(temp, setpoint=None, history=(), peers=(), rate=None,
             peer_rates=(), own_past_rates=(), th=DEFAULT):
    """Прогоняет все четыре правила, возвращает список сработавших."""
    out = []
    for f in (
        r1_absolute(temp, setpoint, th),
        r2_condition_band(temp, list(history), th),
        r3_peer(temp, list(peers), th),
        r4_trend(rate, list(peer_rates), list(own_past_rates), th) if rate is not None else None,
    ):
        if f is not None:
            out.append(f)
    return out


def worst(findings):
    """Итоговый уровень по списку сработок: alarm > warn > None."""
    if any(f.level == "alarm" for f in findings):
        return "alarm"
    if findings:
        return "warn"
    return None


def _self_check():
    # R1: уставка 80, при 72 — предупреждение (90 %), при 81 — тревога.
    assert r1_absolute(60.0, 80.0) is None
    assert r1_absolute(73.0, 80.0).level == "warn"
    assert r1_absolute(81.0, 80.0).level == "alarm"
    assert r1_absolute(999.0, None) is None  # уставки нет — молчим
    # Нижняя уставка: в журнале заказчика «Температура ниже 3ºC» встречается
    # 1640 раз за 2025 год. Без неё остывшая камера проходит молча.
    assert r1_absolute(2.0, 80.0, setpoint_low=3.0).level == "alarm"
    assert r1_absolute(2.0, None, setpoint_low=3.0).level == "alarm"  # только нижняя
    assert r1_absolute(10.0, 80.0, setpoint_low=3.0) is None          # между уставками
    assert r1_absolute(2.0, 80.0) is None                             # нижней не задали

    # R2: зимой при -20 и той же нагрузке элемент обычно 30 ± ~1 °C.
    hist = [29.5, 30.1, 30.4, 29.8, 30.0, 30.2, 29.9, 30.3, 30.1, 29.7, 30.0, 30.2]
    assert r2_condition_band(30.2, hist) is None
    hot = r2_condition_band(36.0, hist)
    assert hot is not None and hot.level == "alarm", hot
    assert r2_condition_band(36.0, hist[:5]) is None  # мало истории — молчим

    # R3: три фазы, одна на 8.5 °C горячее двух других.
    assert r3_peer(41.0, [40.0, 40.5, 41.0]) is None
    warn = r3_peer(48.5, [40.0, 40.0, 40.5])
    assert warn is not None and warn.level == "warn" and abs(warn.value - 8.5) < 1e-9, warn
    alarm = r3_peer(51.0, [40.0, 40.0, 40.5])
    assert alarm.level == "alarm", alarm
    assert r3_peer(99.0, [40.0]) is None  # один сосед — не база для сравнения
    # Медиана настоящая, а не верхнее из двух средних. На чётном списке разница
    # молчаливая и всегда в одну сторону: база завышается, предупреждение не выдаётся.
    assert median([40.0, 50.0]) == 45.0
    assert r3_peer(50.0, [40.0, 50.0]) is not None, "чётный список: медиана 45, дельта 5"
    assert r3_peer(50.0, [40.0, 50.0]).level == "warn"

    # R4: растём на 0.6 °C/ч, соседи на 0.2 — втрое быстрее.
    assert r4_trend(0.1, [0.02]) is None            # рост ниже порога шума
    assert r4_trend(0.55, [0.3]) is None            # меньше чем вдвое — молчим
    assert r4_trend(0.6, [0.3]) is not None         # ровно вдвое — уже сработка
    t = r4_trend(0.6, [0.2])
    assert t is not None and abs(t.value - 0.6) < 1e-9, t
    assert r4_trend(0.6, [], [0.15]) is not None    # сравнение с собой в прошлом

    # Сборка: перегретая фаза, которая ещё не дошла до уставки.
    fs = evaluate(48.5, setpoint=80.0, history=hist, peers=[40.0, 40.0, 40.5],
                  rate=0.6, peer_rates=[0.2])
    rules = {f.rule for f in fs}
    assert rules == {"R2_CONDITION", "R3_PEER", "R4_TREND"}, rules
    assert worst(fs) == "alarm"
    assert worst([]) is None

    print("самопроверка пройдена, пример разбора:")
    for f in fs:
        print("  [%s] %s: %s" % (f.level, f.rule, f.explain))


if __name__ == "__main__":
    _self_check()
