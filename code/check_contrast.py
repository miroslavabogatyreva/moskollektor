#!/usr/bin/env python3
"""Контраст полоски риска у строки дашборда — не ниже 3:1. Строка приёмки НФ-51.

Полоска 3 px слева у строки дашборда красится функцией цветРиска() из
frontend/src/screens/dashboard/rows.ts. До MOS-177 для высокого риска она брала
токен заливки --risk-critical, и в тёмной теме полоска давала 1,07:1 — её не было
видно вовсе. Проверок цвета у нас не было ни одной: check-a11y.mjs смотрит дерево
доступности, а не краски.

Что делаем. Берём из rows.ts, какой токен идёт на high и на normal, из
frontend/src/styles/tokens.css — его значение в каждом из трёх блоков темы
(светлая :root, тёмная по prefers-color-scheme, тёмная по data-theme) и считаем
контраст по формуле WCAG 2.1 против --bg-app того же блока. Фон --bg-app, а не
--bg-surface: браузером 27.09.2026 строки дашборда лежат прямо на body, у которого
background: var(--bg-app). Ветку «класса нет» не меряем: она нарочно неприметна
(нейтраль рамки), и на живых данных класс есть у всех 3 173 участков.

Токен не нашёлся — СБОЙ, а не пропуск: тихо пропущенная тема и есть та дыра,
через которую прошёл MOS-177.

Запуск:
    python3 code/check_contrast.py
    python3 code/check_contrast.py --selftest
Код возврата 1 при СБОЕ.
"""

import re
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
ТОКЕНЫ = КОРЕНЬ / "frontend/src/styles/tokens.css"
СТРОКИ = КОРЕНЬ / "frontend/src/screens/dashboard/rows.ts"
НОРМА = 3.0  # WCAG 1.4.11, нетекстовые элементы
ФОН = "--bg-app"
# Заголовок блока темы в tokens.css → как называем тему в выводе.
ТЕМЫ = {
    ":root {": "светлая",
    ":root:not([data-theme='light']) {": "тёмная (prefers-color-scheme)",
    "[data-theme='dark'] {": "тёмная (data-theme)",
}


def яркость(hex_: str) -> float:
    к = [int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    к = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in к]
    return 0.2126 * к[0] + 0.7152 * к[1] + 0.0722 * к[2]


def контраст(a: str, b: str) -> float:
    la, lb = sorted([яркость(a), яркость(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def блоки(css: str) -> dict[str, dict[str, str]]:
    """Тема → {токен: #rrggbb}. Блок — от заголовка до первой «}»."""
    out = {}
    for заголовок, тема in ТЕМЫ.items():
        i = css.find(заголовок)
        if i < 0:
            continue
        тело = css[i + len(заголовок) : css.index("}", i)]
        out[тема] = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", тело))
    return out


def токены_полоски(ts: str) -> dict[str, str]:
    """Класс риска → токен, который цветРиска() отдаёт полоске."""
    return dict(re.findall(r"cls === '(high|normal)'\) return 'var\((--[\w-]+)\)'", ts))


def проверить(css: str, ts: str) -> list[str]:
    строки = []
    полоска = токены_полоски(ts)
    for кл in ("high", "normal"):
        if кл not in полоска:
            строки.append(
                f"СБОЙ   цветРиска('{кл}') в rows.ts не найден — мерить нечего"
            )
    темы = блоки(css)
    for тема in ТЕМЫ.values():
        if тема not in темы:
            строки.append(f"СБОЙ   {тема}: блока темы в tokens.css нет")
            continue
        т = темы[тема]
        for кл, токен in полоска.items():
            if токен not in т or ФОН not in т:
                строки.append(f"СБОЙ   {тема} {кл}: нет {токен} или {ФОН} в блоке")
                continue
            к = контраст(т[токен], т[ФОН])
            вид = "OK   " if к >= НОРМА else "СБОЙ "
            строки.append(
                f"{вид}  {тема} {кл}: {токен} {т[токен]} против {т[ФОН]} — {к:.2f}:1"
            )
    return строки


def selftest() -> None:
    ts_старый = "if (cls === 'high') return 'var(--risk-critical)'\nif (cls === 'normal') return 'var(--risk-low-border)'"
    ts_новый = ts_старый.replace("--risk-critical)", "--risk-critical-border)")
    css = (
        ":root {\n --bg-app: #e8ebee; --risk-critical: #a81f16; --risk-critical-border: #7a1610; --risk-low-border: #5d8a70; }\n"
        ":root:not([data-theme='light']) {\n --bg-app: #131a22; --risk-critical: #3a1310; --risk-critical-border: #d9534a; --risk-low-border: #3e7a57; }\n"
        "[data-theme='dark'] {\n --bg-app: #131a22; --risk-critical: #3a1310; --risk-critical-border: #d9534a; --risk-low-border: #3e7a57; }\n"
    )
    # Случай MOS-177: токен заливки в тёмной теме — 1,07:1, два СБОЯ (обе тёмные).
    старый = проверить(css, ts_старый)
    assert sum(с.startswith("СБОЙ") for с in старый) == 2, старый
    assert any("1.07:1" in с for с in старый), старый
    # Парный токен границы — шесть OK из шести.
    новый = проверить(css, ts_новый)
    assert len(новый) == 6 and all(с.startswith("OK") for с in новый), новый
    # Пропавший блок темы и пропавший токен — СБОЙ, а не тишина.
    assert any(
        "блока темы" in с for с in проверить(css.split("[data-theme")[0], ts_новый)
    )
    assert any(
        "нет --risk-low-border" in с
        for с in проверить(
            css.replace(" --risk-low-border: #3e7a57;", "", 1).replace(
                " --risk-low-border: #5d8a70;", ""
            ),
            ts_новый,
        )
    )
    assert any("мерить нечего" in с for с in проверить(css, ""))
    # Формула сверена с числами задачи MOS-177: #d9534a на #1b242e — 3,95:1.
    assert f"{контраст('#d9534a', '#1b242e'):.2f}" == "3.95"
    print("самопроверка контраста: 5 случаев пройдены")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    итог = проверить(
        ТОКЕНЫ.read_text(encoding="utf-8"), СТРОКИ.read_text(encoding="utf-8")
    )
    print("\n".join(итог))
    sys.exit(1 if any(с.startswith("СБОЙ") for с in итог) else 0)
