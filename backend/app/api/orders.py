"""Заявки со снимком исходного предупреждения или прогнозом старого формата.

Конец окна риска не является предсказанным временем отказа. Срок реакции
и оставшаяся часть окна выводятся раздельно.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.auth.deps import require
from app.db import get_conn
from app.domain.section_map import get_section_mapping, SECTION_MAP_SQL, map_section
from app.domain.order_rules import запас_часов, конец_окна

router = APIRouter(prefix="/api")

FROM_SQL = """
  FROM maint.notification n
  JOIN asset.func_location l ON l.id = n.func_location_id
  JOIN ref.object_xref x     ON x.func_location_id = l.id
  JOIN maint.work_order wo   ON wo.notification_id = n.id
  JOIN ref.activity_type act ON act.id = wo.activity_type_id
  JOIN ref.priority p        ON p.id = n.priority_id
  LEFT JOIN pred.forecast f ON f.forecast_id = n.forecast_id
  LEFT JOIN pred.run r ON r.run_id = f.run_id
  LEFT JOIN pred.warning_section ws ON ws.id=n.warning_section_id
  LEFT JOIN pred.warning w ON w.warning_key=ws.warning_key
 WHERE f.forecast_id IS NOT NULL OR w.warning_key IS NOT NULL
"""

COUNT_SQL = f"SELECT count(*) {FROM_SQL}"

# count(*) OVER() жил бы внутри строк LIST_SQL, а за последней страницей строк
# нет — total подставлялся бы нулём вместо настоящего числа заявок (нашла 28,
# 21.09.2026: GET /api/orders?offset=1000 отвечал total=0 при 224 заявках).
# Отдельный COUNT_SQL от страницы не зависит, как и у /api/forecasts.
LIST_SQL = f"""
SELECT n.id, x.section_id, l.name AS object_name, x.smvu_key, act.name AS work_type_name,
       n.due_at, n.reported_at, n.status, p.code AS priority_code, COALESCE(w.opened_at,r.as_of) AS as_of,
       COALESCE(w.horizon_h,f.horizon_h) AS horizon_h
{FROM_SQL}
 ORDER BY n.due_at
 LIMIT $1 OFFSET $2
"""

DETAIL_SQL = """
SELECT n.id, n.notification_no, n.notification_kind, n.status, n.source_system,
       n.subject, n.reported_at, n.due_at, n.long_text AS reason,
       n.warning_opened_at, n.risk_window_end,
       n.created_at, n.created_by,
       x.section_id, x.smvu_key,
       l.id AS func_location_id, l.code AS func_location_code, l.name AS object_name,
       cr.code AS criticality_code, cr.name AS criticality_name,
       ot.code AS order_type_code, ot.name AS order_type_name,
       act.code AS activity_type_code, act.name AS activity_type_name,
       wo.order_no, wo.status AS work_order_status,
       wo.planned_start, wo.planned_finish,
       p.code AS priority_code, p.name AS priority_name, p.response_hours,
       f.forecast_id, COALESCE(f.run_id,w.first_run_id) AS run_id,
       COALESCE(w.opened_at,r.as_of) AS as_of,
       COALESCE(f.direction,'sensor_failure') AS direction,
       COALESCE(w.horizon_h,f.horizon_h) AS horizon_h,
       COALESCE(w.probability,f.probability) AS probability, f.risk_rank,
       w.warning_key, w.expires_at, w.features AS opening_features,
       -- Класс критичности участка проставлен db/migrations/010_orders.sql по
       -- системам его каналов (smvu.channel.system_kind) — причина берётся тем
       -- же способом, а не выдумывается заново; пусто только у класса C, для
       -- него текст собирает Python.
       (SELECT 'на участке есть канал системы «' ||
               string_agg(DISTINCT ch.system_kind, '» и «' ORDER BY ch.system_kind) || '»'
          FROM smvu.channel ch
         WHERE ch.section_id = x.section_id
           AND ch.system_kind = ANY(
               CASE cr.code
                   WHEN 'A' THEN ARRAY['Пожарная охрана', 'Газовая охрана']
                   WHEN 'B' THEN ARRAY['Охранная подсистема', 'Диспетчерский контроль']
                   ELSE ARRAY[]::text[]
               END)
       ) AS criticality_reason
  FROM maint.notification n
  JOIN asset.func_location l  ON l.id = n.func_location_id
  JOIN ref.object_xref x      ON x.func_location_id = l.id
  LEFT JOIN ref.criticality cr ON cr.id = l.criticality_id
  JOIN maint.work_order wo    ON wo.notification_id = n.id
  JOIN ref.order_type ot      ON ot.id = wo.order_type_id
  JOIN ref.activity_type act  ON act.id = wo.activity_type_id
  JOIN ref.priority p         ON p.id = n.priority_id
  LEFT JOIN pred.forecast f ON f.forecast_id = n.forecast_id
  LEFT JOIN pred.run r ON r.run_id = f.run_id
  LEFT JOIN pred.warning_section ws ON ws.id=n.warning_section_id
  LEFT JOIN pred.warning w ON w.warning_key=ws.warning_key
 WHERE n.id = $1 AND (f.forecast_id IS NOT NULL OR w.warning_key IS NOT NULL)
"""


def _часов(от, до) -> float:
    return round((до - от).total_seconds() / 3600, 1)


def название_участка(location, key):
    if location.get("collector_name"):
        return f"{location['collector_name']}, пикет {location['picket']}"
    return f"Участок {key}, коллектор не определён однозначно"


@router.get("/orders")
async def list_orders(
    limit: int = Query(200, ge=1, le=1000, description="сколько записей вернуть, потолок 1000"),
    offset: int = Query(0, ge=0, description="сколько записей пропустить от начала выборки"),
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("orders.read")),
):
    total = await conn.fetchval(COUNT_SQL)
    rows = await conn.fetch(LIST_SQL, limit, offset)
    locations = {r["section_id"]: map_section(r) for r in await conn.fetch(SECTION_MAP_SQL)}
    return {
        "schema_version": "orders.v1",
        "total": total,
        "items": [
            {
                "id": r["id"],
                "object_name": название_участка(locations.get(r["section_id"], {}), r["smvu_key"]),
                "smvu_key": r["smvu_key"],
                "work_type_name": r["work_type_name"],
                "due_at": r["due_at"],
                "deadline_hours": _часов(r["reported_at"], r["due_at"]),
                "window_remaining_after_due_h": round(запас_часов(r["due_at"], r["as_of"], r["horizon_h"]), 1),
                "status": r["status"],
                "priority_code": r["priority_code"],
            }
            for r in rows
        ],
    }


@router.get("/orders/{order_id}")
async def get_order(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("orders.read")),
):
    row = await conn.fetchrow(DETAIL_SQL, order_id)
    if row is None:
        raise HTTPException(404, "заявка не найдена")

    location = await get_section_mapping(conn, row["section_id"])
    criticality_reason = row["criticality_reason"] or (
        f"класс критичности «{row['criticality_name']}» — "
        "выделенных систем на участке нет"
    )

    return {
        "schema_version": "orders.v1",
        "id": row["id"],
        "notification_no": row["notification_no"],
        "notification_kind": row["notification_kind"],
        "status": row["status"],
        "source_system": row["source_system"],
        "subject": row["subject"],
        "reported_at": row["reported_at"],
        "due_at": row["due_at"],
        "deadline_hours": _часов(row["reported_at"], row["due_at"]),
        "warning_opened_at": row["warning_opened_at"],
        "risk_window_end": row["risk_window_end"],
        "object": {
            "section_id": row["section_id"],
            "smvu_key": row["smvu_key"],
            **location,
            "func_location_id": row["func_location_id"],
            "func_location_code": row["func_location_code"],
            "name": название_участка(location, row["smvu_key"]),
            "criticality_code": row["criticality_code"],
            "criticality_name": row["criticality_name"],
            "criticality_reason": criticality_reason,
        },
        "work_type": {
            "order_type_code": row["order_type_code"],
            "order_type_name": row["order_type_name"],
            "activity_type_code": row["activity_type_code"],
            "activity_type_name": row["activity_type_name"],
        },
        "work_order": {
            "order_no": row["order_no"],
            "status": row["work_order_status"],
            "planned_start": row["planned_start"],
            "planned_finish": row["planned_finish"],
        },
        "priority": {
            "code": row["priority_code"],
            "name": row["priority_name"],
            "response_hours": row["response_hours"],
        },
        "forecast": {
            "forecast_id": row["forecast_id"],
            "run_id": row["run_id"],
            "as_of": row["as_of"],
            "direction": row["direction"],
            "horizon_h": row["horizon_h"],
            "probability": row["probability"],
            "risk_rank": row["risk_rank"],
            "risk_window_ends_at": row["expires_at"] or конец_окна(row["as_of"], row["horizon_h"]),
            "warning_id": row["warning_key"],
            "opening_features": json.loads(row["opening_features"]) if isinstance(row["opening_features"], str) else row["opening_features"],
            "window_remaining_after_due_h": round(запас_часов(row["due_at"], row["as_of"], row["horizon_h"]), 1),
        },
        "reason": row["reason"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }
