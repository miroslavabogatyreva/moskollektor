"""Словарь значений `val` журнала СМВУ: каждое текстовое значение — одна категория.

Зачем. Строки вроде «Неисправен» раньше жили в трёх местах (`config`, `failure_defs`,
`guard`), и опечатка в одном молча обнулила бы признак или отказ: SQL `val = '…'` с
несуществующей строкой не падает, а считает ноль. Здесь строки записаны один раз, а
всё остальное их импортирует.

Словарь снят с полной выгрузки 2019-01 … 2026-06 (`j*.parquet`, 313,5 млн строк): 32
текстовых значения, 225,1 млн чисел и 131,5 тыс. отметок времени вида
`01.01.1970 03:00:01`. `NULL` в выгрузке нет. Значение, которого нет в словаре, —
категория `unknown`: оно не входит ни в одно определение отказа и ни в один признак,
поэтому расчёт на момент сообщает о нём (`unknown_values` в `score.v3`), а не молчит.

Категории описательные: определение отказа D5 задаёт `failure_defs.D5_VALUES`, а не
категория, — в D5 входят значения трёх категорий.
"""

from __future__ import annotations

import re

# --- значения, на которые ссылается код -------------------------------------------
FAULT = "Неисправен"
UNDEF = "Неопределен"                  # без «о»: так пишет СМВУ, это не «Не определено»
NOT_DEFINED = "Не определено"
MANY_FAULTS = "Много неисправных устройств"
DEENERGIZED = "Обесточен"
BATTERY_FAULT = "Батарея неисправна"
ON_BATTERY = "Питание от батарей"
NORMAL = "Норма"
GUARD_OFF = "Снято с охраны"
GUARD_ON = "На охране"
DEVICE_OFF = "Отключено устройство"

# --- категории ------------------------------------------------------------------
FAULT_CAT = "fault"        # устройство сообщает о своей неисправности
LOSS = "loss"              # состояние не определено: связь или опрос потеряны
POWER = "power"            # питание: сеть, батареи, обесточивание
BATTERY = "battery"        # состояние батареи
EVENT = "event"            # сработка датчика: дым, газ, вода, движение, вызов
NORMAL_CAT = "normal"      # возврат в норму после сработки или неисправности
STATE = "state"            # положение исполнительного устройства
GUARD = "guard"            # постановка и снятие с охраны
SERVICE = "service"        # устройство отключено оператором
NUMERIC = "numeric"        # показание датчика числом
TIMESTAMP = "timestamp"    # отметка времени строкой
UNKNOWN = "unknown"        # значения нет в словаре — новое для модели

CATEGORY: dict[str, str] = {
    FAULT: FAULT_CAT,
    MANY_FAULTS: FAULT_CAT,
    UNDEF: LOSS,
    NOT_DEFINED: LOSS,
    DEENERGIZED: POWER,
    "Есть питание": POWER,
    "Питание от сети": POWER,
    ON_BATTERY: POWER,
    BATTERY_FAULT: BATTERY,
    "Батарея разряжена": BATTERY,
    "Обнаружено движение": EVENT,
    "Обнаружен дым": EVENT,
    "Затоплен": EVENT,
    "Обнаружен газ": EVENT,
    "Вызов": EVENT,
    "Разговор": EVENT,
    "Рычаг сдернут": EVENT,
    "Температура ниже 3ºC": EVENT,
    "Температура выше 40ºC": EVENT,
    "Движения нет": NORMAL_CAT,
    NORMAL: NORMAL_CAT,
    "Дыма нет": NORMAL_CAT,
    "Рычаг норма": NORMAL_CAT,
    "В норме от +3 до +40": NORMAL_CAT,
    "Устройства на объекте исправны": NORMAL_CAT,
    "Работают все насосы в АНС": NORMAL_CAT,
    "Включен": STATE,
    "Выключен": STATE,
    "Не замкнут": STATE,
    GUARD_ON: GUARD,
    GUARD_OFF: GUARD,
    DEVICE_OFF: SERVICE,
}

# Отметка времени строкой: 131 508 строк выгрузки, все ровно в этом виде.
TS_PATTERN = r"^\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d$"
_TS_RE = re.compile(TS_PATTERN)


def _is_number(val: str) -> bool:
    """Число — то, что DuckDB приводит `TRY_CAST(val AS DOUBLE)`: так считает `daily`."""
    try:
        float(val)
    except ValueError:
        return False
    return True


def category(val: str | None) -> str:
    """Категория одного значения; `None` и всё, чего нет в словаре, — `unknown`."""
    if val is None:
        return UNKNOWN
    if val in CATEGORY:
        return CATEGORY[val]
    if _TS_RE.match(val):
        return TIMESTAMP
    if _is_number(val):
        return NUMERIC
    return UNKNOWN


def sql_list(vals) -> str:
    """`'a', 'b'` для `IN (…)`: строки словаря без кавычек, экранировать нечего."""
    return ", ".join(f"'{v}'" for v in vals)


def unknown_sql(src: str) -> str:
    """Запрос `(value, rows)` — значения источника `src`, которых нет в словаре.

    Число и отметка времени проверяются так же, как в `category`; `NULL` — тоже
    неизвестное значение: он выпал бы из каждого счётчика молча.
    """
    return f"""
    SELECT val AS value, count(*) AS rows FROM ({src})
    WHERE val IS NULL
       OR (val NOT IN ({sql_list(CATEGORY)})
           AND TRY_CAST(val AS DOUBLE) IS NULL
           AND NOT regexp_matches(val, '{TS_PATTERN}'))
    GROUP BY 1 ORDER BY 2 DESC, 1"""
