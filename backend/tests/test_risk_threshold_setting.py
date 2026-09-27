"""US-23 сц. 1 «Новый порог работает со следующего расчёта» (docs/user-stories.md).

Порог высокого риска на стенде тест не меняет: он красит дашборд всем. E2E сравнивает
класс с текущим порогом, а здесь закреплено, что расчёт (publish.записать) читает
порог из ref.app_setting на каждом прогоне, а не держит его константой: значение,
которое администратор сохранил через PUT /api/settings/risk_threshold_high, решает
класс участков следующего прогона.
"""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

from app.worker import publish


class ПоддельнаяБаза:
    """Отвечает на запросы publish.записать так, как ответила бы база с одной
    правкой администратора в ref.app_setting и пустым текущим прогнозом."""

    def __init__(self, настройки: dict[str, str]):
        self.настройки = настройки
        self.текущее: list[tuple] = []

    async def fetch(self, sql, *args):
        if "ref.app_setting" in sql:
            return [
                {"key": k, "value": Decimal(v)}
                for k, v in self.настройки.items()
                if k in args[0]
            ]
        return []  # прошлого прогноза нет, систем у каналов нет

    async def fetchval(self, sql, *args):
        return None  # разнос выключен

    async def execute(self, sql, *args):
        return "OK"

    async def executemany(self, sql, rows):
        if "pred.forecast_current" in sql:
            self.текущее = list(rows)

    @asynccontextmanager
    async def transaction(self):
        yield


def классы(настройки: dict[str, str], вероятности: list[float]) -> list[str]:
    база = ПоддельнаяБаза(настройки)
    участки = list(range(1, len(вероятности) + 1))
    asyncio.run(
        publish.записать(
            база,
            1,
            None,
            24,
            "sensor_failure",
            участки,
            вероятности,
            [[] for _ in участки],
        )
    )
    return [строка[9] for строка in база.текущее]  # risk_class


def test_threshold_comes_from_settings():
    # 0,65 ниже умолчания 0,80, но выше сохранённого администратором 0,60 плюс гистерезис.
    assert классы(
        {"risk_threshold_high": "0.60", "risk_class_hysteresis": "0.02"}, [0.65, 0.5]
    ) == [
        "high",
        "normal",
    ]


def test_raised_threshold_drops_high():
    # Администратор поднял порог до 0,90 — участок с 0,85 больше не высокий.
    assert классы(
        {"risk_threshold_high": "0.90", "risk_class_hysteresis": "0.02"}, [0.85]
    ) == ["normal"]


def test_default_threshold_without_setting():
    # Строки в ref.app_setting нет — расчёт берёт умолчание 0,80, а не падает.
    assert классы({}, [0.85, 0.65]) == ["high", "normal"]
