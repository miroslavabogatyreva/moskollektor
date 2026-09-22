"""Пороги и горизонт прогноза как данные. Задача MOS-110 (Q4.12).

Читает и правит ref.app_setting (020_app_setting.sql). НФ-44 держит настройки
только у администратора — тот же уровень, что у audit.read (Q4.10), поэтому
и GET здесь ограничен require(), а не открыт всем ролям, как риски и заявки.
"""

from datetime import date, datetime
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")

# Правило на каждый ключ, который сеют миграции 020, 026, 028, 032, 035. Находки 58
# и 57: без границ PUT принимал горизонт 0 и −5. Ключа нет в правилах — 422, а не
# KeyError и 500: так падали 10 ключей из 16 (MOS-159, вопрос Николая в PR #5).
# Новая настройка без правила через API не правится, пока правило не заведут.
#   "prob"  — вероятность, открытый интервал (0, 1): пороги Precision/Recall, риска, заявок
#   "frac"  — доля [0, 1): мёртвая зона и гистерезис, ноль значит «выключено»
#   "int"   — целое не меньше нижней границы; горизонт ≥ 24 ч по постановке,
#             пульс ≥ 1 мин — при нуле publish.py пишет журнал на каждом прогоне
#   "flag"  — 0 или 1
#   "pos"   — строго больше нуля: alpha = 0 обнуляет долю участков без отказов
_RULES = {
    "forecast_horizon_h": ("int", Decimal(24)),
    "forecast_heartbeat_min": ("int", Decimal(1)),
    "risk_class_hold_min": ("int", Decimal(0)),
    "order_top_sections_per_object": ("int", Decimal(0)),
    "precision_min": ("prob", None),
    "recall_min": ("prob", None),
    "risk_threshold_high": ("prob", None),
    "order_threshold_a": ("prob", None),
    "order_threshold_b": ("prob", None),
    "order_threshold_c": ("prob", None),
    "forecast_deadband": ("frac", None),
    "risk_class_hysteresis": ("frac", None),
    "forecast_spread_enabled": ("flag", None),
    "forecast_weight_alpha": ("pos", None),
}

# Окно истории отказов для веса участка (032_section_weight_window.sql, MOS-159):
# дата числом ГГГГММДД, потому что колонка value числовая. Без проверки PUT принял
# бы 20221399, и pred.weight_window() падал бы на to_date при каждом чтении веса —
# то есть сломался бы разнос в воркере, а не запрос администратора.
_WINDOW_FROM = "forecast_weight_window_from"
_WINDOW_TO = "forecast_weight_window_to"
_WINDOW_PAIR = {_WINDOW_FROM: _WINDOW_TO, _WINDOW_TO: _WINDOW_FROM}


def _as_date(value: Decimal) -> date | None:
    if value != value.to_integral_value():
        return None
    текст = str(int(value))
    # Ровно восемь цифр: strptime прочтёт и «2022041» как 2022-04-01, а to_date
    # в pred.weight_window() — по-своему, и API с базой разошлись бы в дате.
    if len(текст) != 8 or not текст.isdigit():
        return None
    try:
        return datetime.strptime(текст, "%Y%m%d").date()
    except ValueError:
        return None


def _window_order_error(key: str, value: Decimal, other: Decimal | None) -> str | None:
    """«С» позже «по» — окно пустое, и вес молча становится ровным 1/N у всех участков."""
    if other is None:
        return None
    start, end = (value, other) if key == _WINDOW_FROM else (other, value)
    if _as_date(start) > _as_date(end):
        return f"окно веса: начало {start} позже конца {end}"
    return None


def _validation_error(key: str, value: Decimal) -> str | None:
    if key in _WINDOW_PAIR:
        if _as_date(value) is None:
            return f"{key}: дата числом ГГГГММДД, например 20220401"
        return None
    if key not in _RULES:
        return f"{key}: для настройки нет правила проверки, правка через API закрыта"
    kind, low = _RULES[key]
    if kind == "int" and (value != value.to_integral_value() or value < low):
        return f"{key}: целое число, не меньше {low}"
    if kind == "prob" and not (0 < value < 1):
        return f"{key}: значение должно быть в интервале (0, 1)"
    if kind == "frac" and not (0 <= value < 1):
        return f"{key}: значение должно быть в интервале [0, 1)"
    if kind == "flag" and value not in (0, 1):
        return f"{key}: 0 или 1"
    if kind == "pos" and value <= 0:
        return f"{key}: значение должно быть больше 0"
    return None


class SettingUpdate(BaseModel):
    # Decimal, не float: пороги сравниваются строго ("Precision > 0.7" —
    # code/predictive_metrics.py), а float(0.7) с плавающей рассинхронизирует
    # текст в аудите с тем, что реально легло в numeric-колонку.
    value: Decimal


@router.get("/settings")
async def list_settings(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("settings.read")),
):
    rows = await conn.fetch(
        "SELECT key, value, unit, changed_by, changed_at FROM ref.app_setting ORDER BY key"
    )
    return [dict(r) for r in rows]


@router.put("/settings/{key}")
async def update_setting(
    key: str,
    body: SettingUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    user: asyncpg.Record = Depends(require("settings.write")),
):
    old = await conn.fetchval("SELECT value FROM ref.app_setting WHERE key = $1", key)
    if old is None:
        raise HTTPException(404, f"настройки «{key}» нет")
    ошибка = _validation_error(key, body.value)
    if ошибка is None and key in _WINDOW_PAIR:
        other = await conn.fetchval(
            "SELECT value FROM ref.app_setting WHERE key = $1", _WINDOW_PAIR[key]
        )
        ошибка = _window_order_error(key, body.value, other)
    if ошибка is not None:
        raise HTTPException(422, ошибка)
    row = await conn.fetchrow(
        """
        UPDATE ref.app_setting
        SET value = $1, changed_by = $2, changed_at = now()
        WHERE key = $3
        RETURNING key, value, unit, changed_by, changed_at
        """,
        body.value,
        user["user_id"],
        key,
    )
    # Middleware в app.api.main дописывает эту строку в тот же ряд audit.user_action,
    # который он и так пишет на каждый запрос — второго INSERT не нужно.
    request.state.audit_details = {"old": str(old), "new": str(body.value)}
    return dict(row)
