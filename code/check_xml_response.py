#!/usr/bin/env python3
"""XML-ответ API должен нести те же значения, что и JSON: строка Ф-80.

**Зачем именно это.** `backend/app/api/xml.py` берёт готовый JSON-ответ и
перекладывает его в XML в middleware (`backend/app/api/main.py`). Что метод
собрал 200 и XML разобрался стандартным парсером — не доказывает, что в нём
не потерялось поле: сериализатор может молча уронить ключ, для которого
не нашлось правила. Сверяем не форму, а **значения**: каждый лист XML-дерева
против того же пути в JSON, и печатаем число сверенных листьев — ноль листьев
не проверка, а видимость проверки (проверка не должна одобрять пустоту).

Логика сравнения здесь не импортирует `app.api.xml` — она написана заново
по тем же трём правилам раскладки (список → `<item>`, ключ → имя элемента
или `<field name="…">`, null → `nil="true"`), чтобы проверять сериализатор,
а не звать его же для проверки самого себя.

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k python3 code/check_xml_response.py
    python3 code/check_xml_response.py --selfcheck
"""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROWS = "Ф-80"
ЭНДПОИНТЫ = ["/api/risks", "/api/forecasts?limit=1000"]


def найти_ребёнка(элемент, ключ):
    for ребёнок in элемент:
        if ребёнок.tag == ключ:
            return ребёнок
        if ребёнок.tag == "field" and ребёнок.get("name") == ключ:
            return ребёнок
    return None


def сравнить(элемент, значение, путь):
    """Возвращает (число сверенных листьев, список расхождений)."""
    if isinstance(значение, dict):
        листьев, ошибки = 0, []
        for ключ, вложенное in значение.items():
            ребёнок = найти_ребёнка(элемент, ключ)
            if ребёнок is None:
                ошибки.append(f"{путь}: нет элемента для ключа {ключ!r}")
                continue
            n, e = сравнить(ребёнок, вложенное, f"{путь}.{ключ}")
            листьев += n
            ошибки += e
        return листьев, ошибки
    if isinstance(значение, list):
        дети = [ч for ч in элемент if ч.tag == "item"]
        if len(дети) != len(значение):
            return 0, [
                f"{путь}: в JSON {len(значение)} элементов, в XML {len(дети)} <item>"
            ]
        листьев, ошибки = 0, []
        for i, (ч, v) in enumerate(zip(дети, значение)):
            n, e = сравнить(ч, v, f"{путь}[{i}]")
            листьев += n
            ошибки += e
        return листьев, ошибки
    if значение is None:
        if элемент.get("nil") != "true":
            return 0, [
                f'{путь}: JSON null, а в XML нет nil="true" (текст {элемент.text!r})'
            ]
        return 1, []
    ожидаем = (
        "true" if значение is True else "false" if значение is False else str(значение)
    )
    if (элемент.text or "") != ожидаем:
        return 0, [f"{путь}: JSON {значение!r}, а в XML текст {элемент.text!r}"]
    return 1, []


def _selfcheck():
    json_ok = {"a": 1, "b": None, "c": [1, 2], "3bad": "x"}
    xml_ok = ET.fromstring(
        '<response><a>1</a><b nil="true"/><c><item>1</item><item>2</item></c>'
        '<field name="3bad">x</field></response>'
    )
    n, e = сравнить(xml_ok, json_ok, "response")
    assert e == [], f"верный документ не должен давать расхождений: {e}"
    assert n == 5, f"пять листьев: a, b, c[0], c[1], field/3bad — получили {n}"

    xml_dropped_field = ET.fromstring("<response><a>1</a></response>")
    n, e = сравнить(xml_dropped_field, {"a": 1, "b": 2}, "response")
    assert e and "b" in e[0], "потерянный ключ обязан попасть в список расхождений"

    xml_bad_null = ET.fromstring("<response><a>x</a></response>")
    n, e = сравнить(xml_bad_null, {"a": None}, "response")
    assert e, 'null в JSON без nil="true" в XML — расхождение'

    xml_bad_list = ET.fromstring("<response><c><item>1</item></c></response>")
    n, e = сравнить(найти_ребёнка(xml_bad_list, "c"), [1, 2], "response.c")
    assert e, "разное число элементов списка — расхождение"

    print("самопроверка ok: совпадение, потерянный ключ, битый null, битый список")


def curl(url, accept):
    cmd = ["curl", "-s"] + os.environ.get("CURL_OPTS", "").split()
    cmd += ["-H", f"X-User-Login: {os.environ.get('API_LOGIN', 'dispatcher1')}"]
    cmd += ["-H", f"Accept: {accept}", url]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout


def main():
    if "--selfcheck" in sys.argv:
        _selfcheck()
        return 0
    _selfcheck()
    base = os.environ.get("BASE_URL")
    if not base:
        print(f"{ROWS} СБОЙ: не задан BASE_URL")
        return 1

    всего_листьев, все_ошибки = 0, []
    for путь_запроса in ЭНДПОИНТЫ:
        url = f"{base}{путь_запроса}"
        тело_json = curl(url, "application/json")
        тело_xml = curl(url, "application/xml")
        try:
            значение = json.loads(тело_json)
            корень = ET.fromstring(тело_xml)
        except Exception as e:
            все_ошибки.append(
                f"{путь_запроса}: ответ не разобрался — {type(e).__name__}: {e}"
            )
            continue
        листьев, ошибки = сравнить(корень, значение, путь_запроса)
        всего_листьев += листьев
        все_ошибки += [f"{путь_запроса}: {ош}" for ош in ошибки]
        print(f"  {путь_запроса}: {листьев} листьев сверено, {len(ошибки)} расхождений")

    if все_ошибки:
        print(
            f"{ROWS} СБОЙ: {len(все_ошибки)} расхождений из {всего_листьев} сверенных листьев"
        )
        for ош in все_ошибки[:20]:
            print(f"  - {ош}")
        return 1
    if всего_листьев == 0:
        print(f"{ROWS} СБОЙ: сверять было нечего — 0 листьев")
        return 1
    print(f"{ROWS} OK: {всего_листьев} листьев сверено, 0 расхождений")
    return 0


if __name__ == "__main__":
    sys.exit(main())
