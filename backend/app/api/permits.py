"""Эмулятор реестра нарядов-допусков (US-13, Ф-64, Ф-10, миграция 055).

Реестра нарядов у заказчика для нас нет, схема permit.* (003_permits.sql) пустая.
Администратор (или E2E-тест под admin1) открывает наряд на участке и закрывает его.
Открытый наряд видят карточка участка (GET /api/objects/{id}, поле open_permits)
и расчёт заявок (app.domain.order_rules.в_работах): по участку в работах
превентивная заявка не заводится.
"""
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")


class PermitIn(BaseModel):
    section_id: int
    valid_from: AwareDatetime
    valid_to: AwareDatetime
    work_type_code: str = "OTH"
    work_description: str = Field(min_length=1, max_length=2000)


class PermitOut(BaseModel):
    id: int
    number: str
    section_id: int
    work_type_name: str
    valid_from: datetime
    valid_to: datetime
    closed_at: datetime | None


# Действующие наряды участка — те же условия, что у order_rules.ДЕЙСТВУЮЩИЕ_НАРЯДЫ.
ДЕЙСТВУЮЩИЕ = """
SELECT p.id, p.number, x.section_id, t.name AS work_type_name,
       p.valid_from, p.valid_to, p.closed_at
  FROM permit.permit p
  JOIN ref.object_xref x          ON x.permit_location_id = p.location_id
  JOIN permit.permit_work_type t  ON t.code = p.work_type_code
 WHERE p.closed_at IS NULL AND now() >= p.valid_from AND now() < p.valid_to
   AND x.section_id = $1
 ORDER BY p.valid_to
"""


@router.post("/permits", response_model=PermitOut, status_code=201)
async def open_permit(
    body: PermitIn,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("permits.write")),
):
    """Открыть наряд-допуск на участке (эмулятор реестра, US-13)."""
    if body.valid_to <= body.valid_from:
        raise HTTPException(422, "конец наряда должен быть позже начала")
    xref = await conn.fetchrow(
        "SELECT section_id, smvu_key, permit_location_id FROM ref.object_xref WHERE section_id = $1",
        body.section_id)
    if xref is None or xref["smvu_key"] is None:
        raise HTTPException(404, "участок не найден")
    if not await conn.fetchval(
            "SELECT true FROM permit.permit_work_type WHERE code = $1", body.work_type_code):
        raise HTTPException(422, f"вида работ {body.work_type_code!r} нет в справочнике")

    async with conn.transaction():
        место = xref["permit_location_id"]
        if место is None:
            # Место наряда — участок коллектора, код тот же, что ключ СМВУ «коллектор:пикет».
            место = await conn.fetchval(
                """
                INSERT INTO permit.location (level, code, name) VALUES ('section', $1, $2)
                ON CONFLICT (level, code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                xref["smvu_key"], f"Участок {xref['smvu_key']}")
            await conn.execute(
                "UPDATE ref.object_xref SET permit_location_id = $1 WHERE section_id = $2",
                место, body.section_id)
        номер = f"ЭМ-{body.section_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
        permit_id = await conn.fetchval(
            """
            INSERT INTO permit.permit (number, work_type_code, location_id, valid_from,
                                       valid_to, work_description, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'Открыт')
            RETURNING id
            """,
            номер, body.work_type_code, место, body.valid_from, body.valid_to,
            body.work_description)
    request.state.audit_details = {"permit_id": permit_id, "section_id": body.section_id}
    return await _наряд(conn, permit_id)


@router.post("/permits/{permit_id}/close", response_model=PermitOut)
async def close_permit(
    permit_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("permits.write")),
):
    """Закрыть наряд: участок перестаёт быть «в работах»."""
    if not await conn.fetchval(
            "UPDATE permit.permit SET closed_at = now(), status = 'Закрыт'"
            " WHERE id = $1 AND closed_at IS NULL RETURNING true", permit_id):
        if not await conn.fetchval("SELECT true FROM permit.permit WHERE id = $1", permit_id):
            raise HTTPException(404, "наряд не найден")
    return await _наряд(conn, permit_id)


async def _наряд(conn, permit_id: int) -> dict:
    return dict(await conn.fetchrow(
        """
        SELECT p.id, p.number, x.section_id, t.name AS work_type_name,
               p.valid_from, p.valid_to, p.closed_at
          FROM permit.permit p
          JOIN ref.object_xref x         ON x.permit_location_id = p.location_id
          JOIN permit.permit_work_type t ON t.code = p.work_type_code
         WHERE p.id = $1
        """, permit_id))
