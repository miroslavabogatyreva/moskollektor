#!/usr/bin/env python3
"""Сверяет ключи шаблонов объяснения со списком признаков контракта.

Зачем. `ref.explain_template.feature_code` — это имя признака из
`contracts/features.v1.yaml`, но связать их в базе нечем: признаки живут
в файле контракта, а не в таблице, поэтому внешнему ключу не на что ссылаться.
Значит опечатка в имени не даёт ошибки: строка вставится, бэкенд задачи 7.7
не найдёт шаблон по своему имени признака и покажет диспетчеру прогноз
без объяснения. Молча и на приёмке.

Эта проверка и есть замена отсутствующему внешнему ключу. Гоняем после
каждой правки сида:

    python3 code/check_explain_templates.py

Зависимостей нет: имена берутся регулярным выражением, а не через pyyaml —
его нет в системном python3, а тащить venv ради списка строк незачем.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "db" / "seed" / "explain_templates.sql"
CONTRACT = ROOT / "contracts" / "features.v1.yaml"

# Суффикс к имени признака. Нужен там, где фраза зависит от направления
# отклонения: «писал чаще» и «писал реже» — разные предложения, одним
# шаблоном с числом они не пишутся (см. шапку 006_explain_templates.sql).
SUFFIXES = (".up", ".down")


def contract_features(text):
    """Имена блока features. Блок not_in_v1 не берём: на этих данных
    те признаки не считаются, и шаблон для них означал бы фразу,
    которая никогда не покажется."""
    block = text.split("\nfeatures:", 1)[-1].split("\nnot_in_v1:", 1)[0]
    return set(re.findall(r"^\s+-\s+name:\s*(\S+)", block, re.M))


def seed_rows(text):
    """Пары (feature_code, phrase_ru) из вставок сида."""
    return re.findall(r"\(\s*'([^']+)'\s*,\s*'((?:[^']|'')*)'\s*\)", text)


def check(seed_text, contract_text):
    """Возвращает список претензий. Пустой список — всё сошлось."""
    known = contract_features(contract_text)
    rows = seed_rows(seed_text)
    bad = []

    if not rows:
        return ["в сиде не нашлось ни одной вставки — проверять нечего"]
    if not known:
        return ["в контракте не нашлось ни одного признака — проверять не с чем"]

    seen = set()
    for code, phrase in rows:
        base = code
        for s in SUFFIXES:
            if code.endswith(s):
                base = code[: -len(s)]
                break
        else:
            if "." in code:
                bad.append(f"{code}: суффикс не из {SUFFIXES}")

        if base not in known:
            bad.append(f"{code}: признака «{base}» нет в блоке features контракта")
        if code in seen:
            bad.append(f"{code}: ключ повторяется, вторая строка затрёт первую")
        seen.add(code)

        if phrase.count("{value}") > 1:
            bad.append(f"{code}: токен {{value}} встречается больше одного раза")

    return bad


def _selfcheck():
    """Проверка обязана кричать на заведомо неверном входе. Иначе неизвестно,
    работает ли она вообще: зелёный вывод на одном верном примере
    не отличается от зелёного вывода сломанной проверки."""
    contract = "\nfeatures:\n  - name: bad7\n  - name: rate_ratio_7d\nnot_in_v1:\n  - name: age_years\n"
    ok = "INSERT INTO t VALUES\n    ('bad7', 'фраза {value}'),\n    ('rate_ratio_7d.up', 'фраза');"
    assert check(ok, contract) == [], "верный сид не прошёл"

    cases = [
        ("('bad77', 'фраза')", "опечатка в имени"),
        ("('age_years', 'фраза')", "признак из not_in_v1"),
        ("('rate_ratio_7d.high', 'фраза')", "чужой суффикс"),
        ("('bad7', 'два {value} и {value}')", "два токена подстановки"),
        ("('bad7', 'раз'),\n    ('bad7', 'два')", "повтор ключа"),
    ]
    for body, что in cases:
        assert check(f"INSERT INTO t VALUES\n    {body};", contract), f"не поймано: {что}"
    assert check("", contract), "пустой сид принят за верный"


if __name__ == "__main__":
    _selfcheck()
    претензии = check(SEED.read_text(), CONTRACT.read_text())
    if претензии:
        print(f"найдено проблем: {len(претензии)}")
        for p in претензии:
            print(f"  - {p}")
        sys.exit(1)
    rows = seed_rows(SEED.read_text())
    print(f"шаблонов: {len(rows)}, все ключи есть в contracts/features.v1.yaml")
    print("проблем нет")
