"""Карточка объекта и ряд показаний. Задача MOS-41 (Q4.4), приёмка М-08, Ф-91.

Ф-91 требует ряд показаний и ссылку на внешний источник (камеру), если он
заведён. Камеры (CCTV) в проекте нет и не будет: docs/HLD.md разд. 11.6
разбирает этот же шаг сценария ТЗ и закрывает его не интеграцией с камерами,
а чекбоксом «Проверено по внешним источникам» (pred.feedback.verified_externally,
задача Q5.8) — дешевле на три порядка и решение уже принято, здесь не
переигрываю. Этот метод закрывает свою часть Ф-91: ряд показаний за окно.
"""
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/objects/{section_id}")
async def get_object(
    section_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("objects.read")),
):
    passport = await conn.fetchrow(
        "SELECT section_id, smvu_key, inventory_no FROM ref.object_xref WHERE section_id = $1",
        section_id,
    )
    if passport is None:
        raise HTTPException(404, "объект не найден")

    channels = await conn.fetch(
        """
        SELECT channel_id, tag, name, system_kind, sensor_kind
        FROM smvu.channel
        WHERE section_id = $1 AND is_active
        ORDER BY channel_id
        """,
        section_id,
    )

    current_risk = await conn.fetchrow(
        """
        SELECT fc.run_id, fc.probability, fc.risk_rank, fc.horizon_h,
               fc.computed_at, fc.is_stale, f.direction, f.explanation_ru
        FROM pred.forecast_current fc
        LEFT JOIN pred.forecast f ON f.run_id = fc.run_id AND f.section_id = fc.section_id
        WHERE fc.section_id = $1
        """,
        section_id,
    )

    recent_forecasts = await conn.fetch(
        """
        SELECT f.forecast_id, f.direction, f.probability, f.risk_rank,
               f.explanation_ru, r.started_at AS computed_at
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE f.section_id = $1
        ORDER BY r.started_at DESC
        LIMIT 10
        """,
        section_id,
    )

    return {
        **dict(passport),
        "channels": [dict(c) for c in channels],
        "current_risk": dict(current_risk) if current_risk else None,
        "recent_forecasts": [dict(f) for f in recent_forecasts],
    }


@router.get("/objects/{section_id}/readings")
async def get_object_readings(
    section_id: int,
    from_: date = Query(..., alias="from", description="дата начала окна, включительно"),
    to: date = Query(..., description="дата конца окна, включительно — весь день целиком"),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("objects.read")),
):
    """from/to обязательны и не моменты времени, а даты — обе границы включительны.

    Обязательность — не только про формат: smvu.reading партиционирована помесячно
    (313 млн строк, 109 партиций), запрос без границы по времени обходит их все.
    Расчётная сессия 16.09.2026 поймала на этом 62 секунды там, где ждала долей
    секунды. Верхняя граница в запросе — начало СЛЕДУЮЩЕГО за to дня, сравнение
    строгое: так «from=to=сегодня» отдаёт весь сегодняшний день, а не пустоту
    (та же ошибка на границе, что нашли в GET /api/forecasts, здесь исправлена сразу).
    """
    exists = await conn.fetchval("SELECT 1 FROM ref.object_xref WHERE section_id = $1", section_id)
    if exists is None:
        raise HTTPException(404, "объект не найден")

    to_exclusive = to + timedelta(days=1)
    rows = await conn.fetch(
        """
        SELECT read_time, channel_id, is_alarm, value_text, value_num
        FROM smvu.reading
        WHERE section_id = $1 AND read_time >= $2 AND read_time < $3
        ORDER BY read_time
        """,
        section_id, from_, to_exclusive,
    )
    return [dict(r) for r in rows]
