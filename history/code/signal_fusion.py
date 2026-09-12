#!/usr/bin/env python3
"""Сведение нескольких слабых сигналов в одну оценку риска.

Метод взят из `signal-triangulation-deepresearch.md` проекта «Речевая аналитика»,
раздел 7.3 «Baseline: Weighted Sum Rule». Там четыре источника (текст разговора,
акустика, ролевая атрибуция, бизнес-метаданные) складываются с весами, а
отсутствующий источник заменяется перераспределением весов.

У нас источники другие: газоанализатор, температура/влага, состояние датчика СМВУ,
наряд-допуск и история ремонтов. Математика та же.

Одно отличие от оригинала. В оригинале при пропаже источника веса
перенормируются — и тогда уверенность может вырасти от того, что сигнал пропал.
Это ровно тот провал, который в исследовании описан в разделе 5.2 со ссылкой на
CML (Ma et al., ICML 2023): «confidence мультимодальных моделей может возрастать
при удалении или повреждении модальности». Поэтому здесь две разные величины:

  score[c]      — нормированная оценка класса c, чтобы сравнивать классы между собой;
  confidence    — та же сумма БЕЗ нормировки, то есть дисконтированная на долю
                  недостающих источников. Она не может вырасти от пропажи сигнала.

Проверка монотонности встроена: `monotonicity_violations()` перебирает все
подмножества сигналов и возвращает те, где уверенность выросла при их удалении.
Только стандартная библиотека. Самопроверка: `python3 signal_fusion.py`.
"""

from itertools import combinations

# Веса источников. Сумма по всем источникам = 1.0.
# Смысл веса — сколько доверия источник заслуживает, когда он доступен.
# Аналог в «Речевой аналитике»: linguist 0.35 / prosodist 0.25 / architect 0.25 / identifier 0.15.
DEFAULT_WEIGHTS = {
    "gas": 0.30,  # газоанализатор: метан, CO, H2S, кислород
    "climate": 0.20,  # температура и влажность в камере
    "sensor_health": 0.15,  # самодиагностика датчика СМВУ: связь, дребезг, залипание
    "permit": 0.15,  # наряд-допуск: есть ли сейчас в камере люди и работы
    "repair_history": 0.20,  # история ремонтов и дефектов по этому объекту
}


def fuse(signals, weights=None):
    """Свести оценки нескольких источников в одну.

    signals: {имя_источника: {класс: вероятность}}. Источника нет в словаре —
             значит он недоступен, и его вес просто не участвует.
    Возвращает словарь с полями score, top, confidence, agreement, coverage, missing.
    """
    weights = weights or DEFAULT_WEIGHTS
    unknown = set(signals) - set(weights)
    if unknown:
        raise KeyError(f"нет весов для источников: {sorted(unknown)}")

    present = {name: p for name, p in signals.items() if p}
    if not present:
        raise ValueError("не передан ни один источник")

    coverage = sum(weights[name] for name in present)
    classes = sorted({c for p in present.values() for c in p})

    # Сырая сумма: Σ w_i · p_i(c). Добавление источника может её только увеличить,
    # поэтому уверенность монотонна по числу источников.
    raw = {c: sum(weights[n] * present[n].get(c, 0.0) for n in present) for c in classes}
    top = max(classes, key=lambda c: raw[c])

    # Сколько источников самостоятельно указывают на тот же класс.
    # В пилоте «Речевой аналитики» это была целевая метрика: «доля эпизодов,
    # подкреплённых ≥2 независимыми сигналами ≥ 60%».
    agreement = sum(1 for p in present.values() if max(p, key=p.get) == top)

    return {
        "score": {c: raw[c] / coverage for c in classes},  # нормировано, классы сравнимы
        "top": top,
        "confidence": raw[top],  # НЕ нормировано: пропажа источника уверенность снижает
        "agreement": agreement,
        "coverage": coverage,
        "missing": sorted(set(weights) - set(present)),
    }


def verdict(result, auto_threshold=0.4, min_agreement=2):
    """Что делать с результатом: считать автоматически или отдать человеку.

    Пороги — из пилота «Речевой аналитики»: «доля эпизодов с classification
    confidence ≥ 0.4 → ≥80%» и «≥2 сигнала → high confidence».
    """
    if result["confidence"] < auto_threshold:
        return "human_review"
    if result["agreement"] < min_agreement:
        return "low_confidence"
    return "auto"


def monotonicity_violations(signals, weights=None):
    """Найти случаи, где удаление источника ПОВЫШАЕТ уверенность.

    Пустой список — калибровка в порядке. Непустой — уверенность нельзя
    показывать диспетчеру как есть.
    """
    weights = weights or DEFAULT_WEIGHTS
    full = fuse(signals, weights)
    names = sorted(signals)
    bad = []
    for k in range(1, len(names)):
        for subset in combinations(names, k):
            part = fuse({n: signals[n] for n in subset}, weights)
            if part["confidence"] > full["confidence"] + 1e-12:
                bad.append((subset, part["confidence"], full["confidence"]))
    return bad


def demo():
    # Камера 14/07, вечер. Газ показывает рост метана, климат — норму,
    # самодиагностика датчика — норму, история ремонтов — плохую репутацию узла.
    camera = {
        "gas": {"failure": 0.85, "normal": 0.15},
        "climate": {"failure": 0.30, "normal": 0.70},
        "sensor_health": {"failure": 0.20, "normal": 0.80},
        "repair_history": {"failure": 0.70, "normal": 0.30},
        # наряда-допуска нет: людей в камере нет, источник недоступен
    }
    r = fuse(camera)
    assert r["top"] == "failure", r
    assert r["missing"] == ["permit"], r
    assert abs(r["coverage"] - 0.85) < 1e-9, r
    # 0.30·0.85 + 0.20·0.30 + 0.15·0.20 + 0.20·0.70 = 0.255+0.06+0.03+0.14 = 0.485
    assert abs(r["confidence"] - 0.485) < 1e-9, r["confidence"]
    assert abs(r["score"]["failure"] - 0.485 / 0.85) < 1e-9, r["score"]
    assert r["agreement"] == 2, r  # газ и история — за отказ; климат и здоровье — против
    assert verdict(r) == "auto", verdict(r)

    # Оставили один газоанализатор: он кричит громче всех, но уверенность обязана упасть.
    only_gas = fuse({"gas": camera["gas"]})
    assert only_gas["score"]["failure"] > r["score"]["failure"]  # нормированная оценка выше
    assert abs(only_gas["confidence"] - 0.255) < 1e-9  # 0.30·0.85, вес одного источника
    assert only_gas["confidence"] < r["confidence"]  # а уверенность — ниже
    assert verdict(only_gas) == "human_review", verdict(only_gas)

    # Монотонность: ни одно удаление источника не поднимает уверенность.
    assert monotonicity_violations(camera) == []

    # Тихий случай: все источники говорят «норма» — в аналитику это не идёт.
    quiet = {n: {"failure": 0.1, "normal": 0.9} for n in DEFAULT_WEIGHTS}
    q = fuse(quiet)
    assert q["top"] == "normal" and q["agreement"] == 5, q
    assert abs(q["coverage"] - 1.0) < 1e-9, q
    assert verdict(q) == "auto", verdict(q)

    # Спор: газ за отказ, всё остальное против. Решение остаётся за человеком.
    conflict = {
        "gas": {"failure": 0.95, "normal": 0.05},
        "climate": {"failure": 0.05, "normal": 0.95},
        "sensor_health": {"failure": 0.05, "normal": 0.95},
        "permit": {"failure": 0.05, "normal": 0.95},
        "repair_history": {"failure": 0.10, "normal": 0.90},
    }
    c = fuse(conflict)
    assert c["top"] == "normal" and c["agreement"] == 4, c
    assert monotonicity_violations(conflict) == []

    # Неизвестный источник — падаем громко, а не молча игнорируем.
    try:
        fuse({"vibration": {"failure": 1.0}})
    except KeyError:
        pass
    else:
        raise AssertionError("неизвестный источник должен ронять fuse()")

    print("ok: камера 14/07 →", r["top"],
          f"confidence={r['confidence']:.3f}",
          f"score={r['score']['failure']:.3f}",
          f"сигналов за={r['agreement']}",
          f"нет данных от={r['missing']}",
          "→", verdict(r))


if __name__ == "__main__":
    demo()
