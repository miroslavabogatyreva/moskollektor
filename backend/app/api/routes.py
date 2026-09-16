"""Списочные методы API: риски и прогнозы. Задача MOS-40 (Q4.3), приёмка М-15, М-16.

Пустой результат — 200 и пустой список (Готовность блока Q4, docs/plan.md):
расчёт пишет соседняя сессия, до первого прогона строк в pred.forecast
и pred.forecast_current нет вовсе, и это не повод отвечать ошибкой.
"""
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/risks")
async def list_risks(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("risks.read")),
):
    """Текущий риск по каждому участку — одна строка на участок (М-15)."""
    rows = await conn.fetch(
        """
        SELECT section_id, probability, risk_rank, horizon_h, computed_at, is_stale
        FROM pred.forecast_current
        ORDER BY risk_rank
        """
    )
    return [dict(r) for r in rows]


@router.get("/forecasts")
async def list_forecasts(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("forecasts.read")),
):
    """Прогнозы за период, время расчёта берётся из pred.run.started_at (М-16)."""
    rows = await conn.fetch(
        """
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, r.started_at AS computed_at
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE ($1::timestamptz IS NULL OR r.started_at >= $1)
          AND ($2::timestamptz IS NULL OR r.started_at <= $2)
        ORDER BY r.started_at DESC, f.risk_rank
        """,
        from_, to,
    )
    return [dict(r) for r in rows]


@router.get("/forecasts/{forecast_id}")
async def get_forecast(
    forecast_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("forecasts.read")),
):
    row = await conn.fetchrow(
        """
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, f.factors, r.started_at AS computed_at
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE f.forecast_id = $1
        """,
        forecast_id,
    )
    if row is None:
        raise HTTPException(404, "прогноз не найден")
    return dict(row)
