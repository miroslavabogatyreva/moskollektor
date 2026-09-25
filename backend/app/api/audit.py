"""Журнал действий — только администратору. Задача MOS-47 (Q4.10), задача 4.15
(MOS-135) — период и постраничность, приёмка НФ-77.

Строку в audit.user_action кладёт промежуточный слой в app.api.main, эта задача
только отдаёт журнал наружу.

**До 21.09.2026 метод не принимал ни одного параметра** — жёсткий
`ORDER BY occurred_at DESC LIMIT 500`, `from`/`to`/`limit`/`offset` отбрасывались
молча (нашёл оркестратор: `?limit=10&from=2026-09-21` вернул все 500). Промежуточный
слой пишет строку на КАЖДЫЙ запрос к API, включая проверки — `examples.sh` даёт
36 строк за прогон, — и 500 записей превращались в окно около 75 минут при
1 340 строках в таблице на 21.09.2026. Приёмочный сценарий НФ-77 (десять действий,
затем выгрузка журнала за это время) требует именно период, а не последние N
записей: активность других сессий и проверок успевала вымыть нужные десять
действий из окна ещё до выгрузки.
"""
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.api.schemas import AuditList
from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


@router.get("/audit", response_model=AuditList)
async def list_audit(
    from_: datetime | None = Query(None, alias="from", description="момент начала периода, включительно"),
    to: datetime | None = Query(None, description="момент конца периода, включительно"),
    limit: int = Query(200, ge=1, le=1000, description="сколько записей вернуть, потолок 1000"),
    offset: int = Query(0, ge=0, description="сколько записей пропустить от начала выборки"),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("audit.read")),
):
    """from/to — моменты времени, а не даты: приёмочный сценарий меряет минуты,
    не сутки, дневная граница журнала прогнозов тут ни при чём. total — отдельным
    COUNT(*), не оконной функцией внутри строк: 21.09.2026 та же экономия на
    `GET /api/orders` подставляла total=0 за последней страницей, когда строк
    для окна не оставалось (MOS-117)."""
    where = """
        WHERE ($1::timestamptz IS NULL OR a.occurred_at >= $1)
          AND ($2::timestamptz IS NULL OR a.occurred_at <= $2)
    """
    total = await conn.fetchval(
        f"""
        SELECT count(*)
        FROM audit.user_action a
        {where}
        """,
        from_, to,
    )
    rows = await conn.fetch(
        f"""
        SELECT a.action_id, a.occurred_at, a.method, a.path, a.status_code,
               a.details, u.login, u.full_name
        FROM audit.user_action a
        LEFT JOIN ref.app_user u ON u.user_id = a.user_id
        {where}
        ORDER BY a.occurred_at DESC, a.action_id DESC
        LIMIT $3 OFFSET $4
        """,
        from_, to, limit, offset,
    )
    return {
        "total": total,
        "items": [dict(r) for r in rows],
    }
