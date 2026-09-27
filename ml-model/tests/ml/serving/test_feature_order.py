"""Юнит-тесты проверки порядка признаков и превращения null в NaN.

Модель не нужна: это чистые функции. Поэтому тесты идут всегда.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from ml.serving.app import _to_matrix  # noqa: E402
from ml.serving.model_store import feature_order_problem  # noqa: E402

TRAIN_ORDER = ["pfx_bad7", "pfx_nbr_fail7", "ch_flap7", "rate7_over_base365"]


def test_same_order_is_ok():
    assert feature_order_problem(TRAIN_ORDER, list(TRAIN_ORDER)) is None


def test_swapped_order_names_the_position():
    got = ["pfx_bad7", "ch_flap7", "pfx_nbr_fail7", "rate7_over_base365"]
    msg = feature_order_problem(TRAIN_ORDER, got)
    assert msg is not None
    # Текст обязан называть, где именно расхождение, иначе им нельзя чинить worker.
    assert "позиции 1" in msg
    assert "'pfx_nbr_fail7'" in msg and "'ch_flap7'" in msg
    assert "различается только порядок" in msg


def test_missing_name_is_listed():
    got = ["pfx_bad7", "pfx_nbr_fail7", "ch_flap7"]
    msg = feature_order_problem(TRAIN_ORDER, got)
    assert msg is not None
    assert "Ожидается 4 имён, получено 3" in msg
    assert "Нет в запросе: rate7_over_base365" in msg


def test_extra_name_is_listed():
    got = TRAIN_ORDER + ["age_years"]
    msg = feature_order_problem(TRAIN_ORDER, got)
    assert msg is not None
    assert "Лишние в запросе: age_years" in msg


def test_null_becomes_nan_not_zero():
    """HLD 6.3: null означает «признака нет». Ноль означал бы «ремонтировали сегодня»."""
    matrix = _to_matrix([[None, 0.0, 3.5, None]])
    assert math.isnan(matrix[0, 0])
    assert matrix[0, 1] == 0.0
    assert matrix[0, 2] == 3.5
    assert math.isnan(matrix[0, 3])


def test_all_null_row_is_all_nan():
    matrix = _to_matrix([[None] * 4])
    assert matrix.shape == (1, 4)
    assert bool((matrix != matrix).all())  # NaN != NaN
