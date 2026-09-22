"""Окно веса участка в ref.app_setting (MOS-159): дата числом ГГГГММДД и порядок границ.

Числа — границы периода обучения модели v3 из src/ml/config.py ML-репозитория
(2022-04-01 … 2026-03-31), те же, что сеет 032_section_weight_window.sql.
"""

from decimal import Decimal

import pytest

from app.api.settings import _validation_error, _window_order_error

FROM = "forecast_weight_window_from"
TO = "forecast_weight_window_to"


@pytest.mark.parametrize("key", [FROM, TO])
@pytest.mark.parametrize("value", ["20220401", "20260331", "20240229"])
def test_real_dates_pass(key, value):
    assert _validation_error(key, Decimal(value)) is None


@pytest.mark.parametrize(
    "value",
    [
        "20221399",  # тринадцатый месяц: to_date в базе упал бы на каждом чтении веса
        "20230229",  # 29 февраля не високосного года
        "2022041",  # семь цифр: strptime читает как 2022-04-01, база — иначе
        "20220401.5",  # дробное число — не дата
        "-20220401",
        "0",  # старое «0 — не исключать» от exclude_year здесь не значит ничего
    ],
)
def test_not_a_date_rejected(value):
    assert _validation_error(FROM, Decimal(value)) is not None


def test_order_checked_from_either_side():
    # «с» позже «по» — окно пустое, вес молча становится 1/N у всех участков
    assert _window_order_error(FROM, Decimal("20260401"), Decimal("20260331")) is not None
    assert _window_order_error(TO, Decimal("20220331"), Decimal("20220401")) is not None


def test_order_accepts_model_window_and_single_day():
    assert _window_order_error(FROM, Decimal("20220401"), Decimal("20260331")) is None
    assert _window_order_error(TO, Decimal("20260331"), Decimal("20220401")) is None
    # обе границы включительны, окно в один день законно
    assert _window_order_error(FROM, Decimal("20250101"), Decimal("20250101")) is None


def test_order_skipped_without_pair_row():
    # парной строки нет — сравнивать не с чем, формат уже проверен отдельно
    assert _window_order_error(FROM, Decimal("20220401"), None) is None


def test_other_keys_unchanged():
    assert _validation_error("precision_min", Decimal("0.7")) is None
    assert _validation_error("precision_min", Decimal("1")) is not None
    assert _validation_error("forecast_horizon_h", Decimal("24")) is None
