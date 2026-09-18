"""Журнал действий — только администратору. Задача MOS-47 (Q4.10), приёмка НФ-77.

Строку в audit.user_action кладёт промежуточный слой в app.api.main, эта задача
только отдаёт журнал наружу. LIMIT 500 — не пагинация, а защита от того, что
живой журнал за месяцы работы вернётся одним ответом; на приёмочном сценарии
(десять действий) он не влияет никак.
"""
import asyncpg
from fastapi import APIRouter, Depends

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/audit")
async def list_audit(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("audit.read")),
):
    rows = await conn.fetch(
        """
        SELECT a.action_id, a.occurred_at, a.method, a.path, a.status_code,
               a.details, u.login, u.full_name
        FROM audit.user_action a
        LEFT JOIN ref.app_user u ON u.user_id = a.user_id
        ORDER BY a.occurred_at DESC
        LIMIT 500
        """
    )
    return [dict(r) for r in rows]
