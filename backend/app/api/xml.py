"""Сериализация ответа API в XML. Задача MOS-44 (Q4.7), приёмка Ф-80.

Раскладка: элемент списка — <item>; ключ словаря — имя элемента, а если оно
не годится в имя XML — <field name="…">; null — пустой элемент с nil="true".
"""

import re
import xml.etree.ElementTree as ET

_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
# XML 1.0 запрещает большинство управляющих символов даже экранированными —
# ET.tostring их не проверяет и пишет как есть, а fromstring потом не может
# разобрать собственный вывод. \t \n \r оставляем, они допустимы.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def wants_xml(accept: str, format_param: str | None) -> bool:
    """`?format=xml` побеждает всегда. Иначе — Accept, и по весу (q), а не по
    присутствию подстроки: `Accept: application/json, application/xml;q=0.1`
    просит JSON, а не XML (нашла 59, вердикт по MOS-44). `*/*` не значит
    «просит XML» — это отсутствие явного предпочтения, ответ JSON.
    """
    if format_param == "xml":
        return True
    q = {}
    for часть in accept.split(","):
        тип, _, параметры = часть.strip().partition(";")
        тип = тип.strip().lower()
        if not тип:
            continue
        q[тип] = 1.0
        for п in параметры.split(";"):
            п = п.strip()
            if п.startswith("q="):
                try:
                    q[тип] = float(п[2:])
                except ValueError:
                    pass
    if "application/xml" not in q:
        return False
    return q["application/xml"] > q.get("application/json", 0.0)


def to_xml(data) -> bytes:
    root = ET.Element("response")
    _fill(root, data)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _fill(element: ET.Element, value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            tag = key if _VALID_NAME.match(key) else "field"
            child = ET.SubElement(element, tag)
            if tag == "field":
                child.set("name", key)
            _fill(child, item)
    elif isinstance(value, list):
        for item in value:
            _fill(ET.SubElement(element, "item"), item)
    elif value is None:
        element.set("nil", "true")
    elif isinstance(value, bool):
        element.text = "true" if value else "false"
    else:
        element.text = _CONTROL_CHARS.sub("", str(value))


if __name__ == "__main__":
    assert to_xml({"a": 1, "b": None, "c": [1, 2]}) == (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b'<response><a>1</a><b nil="true" /><c><item>1</item><item>2</item></c></response>'
    )
    assert to_xml({"1bad key": "x"}) == (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b'<response><field name="1bad key">x</field></response>'
    )
    # Управляющие символы (\x01, \x0b) рвут fromstring, если их не вырезать —
    # value_text приходит от заказчика сырым, доверять ему нельзя.
    битый = to_xml({"value_text": "a\x01b\x0bc"})
    assert b"\x01" not in битый and b"\x0b" not in битый
    assert ET.fromstring(битый).find("value_text").text == "abc"

    # Приоритет Accept (нашла 59, MOS-44): вес решает, подстрока — нет.
    assert wants_xml("application/xml", None) is True
    assert wants_xml("application/json, application/xml;q=0.1", None) is False
    assert wants_xml("application/xml, application/json;q=0.5", None) is True
    assert wants_xml("*/*", None) is False
    assert wants_xml("application/json", "xml") is True  # ?format=xml сильнее Accept

    print("ok")
