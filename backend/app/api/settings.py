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

# Границы по ключу — находка 58 и 57: без них PUT принимал горизонт 0 и −5.
# forecast_horizon_h целый и ≥ 24 (постановка: горизонт прогноза не меньше
# 24 часов); пороги и уровень риска — доля в открытом интервале (0, 1).
_BOUNDS = {
    "forecast_horizon_h": (Decimal(24), None),
    "precision_min": (Decimal(0), Decimal(1)),
    "recall_min": (Decimal(0), Decimal(1)),
    "risk_threshold_high": (Decimal(0), Decimal(1)),
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
    if key == "forecast_horizon_h":
        low, _ = _BOUNDS[key]
        if value != value.to_integral_value() or value < low:
            return f"forecast_horizon_h: целое число часов, не меньше {low}"
        return None
    low, high = _BOUNDS[key]
    if not (low < value < high):
        return f"{key}: значение должно быть в интервале ({low}, {high})"
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
