"""Сериализация ответа API в XML. Задача MOS-44 (Q4.7), приёмка Ф-80.

Раскладка: элемент списка — <item>; ключ словаря — имя элемента, а если оно
не годится в имя XML — <field name="…">; null — пустой элемент с nil="true".
"""

import re
import xml.etree.ElementTree as ET

_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


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
        element.text = str(value)


if __name__ == "__main__":
    assert to_xml({"a": 1, "b": None, "c": [1, 2]}) == (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b'<response><a>1</a><b nil="true" /><c><item>1</item><item>2</item></c></response>'
    )
    assert to_xml({"1bad key": "x"}) == (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b'<response><field name="1bad key">x</field></response>'
    )
    print("ok")
