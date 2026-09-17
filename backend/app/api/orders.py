"""Заявки. Задачи MOS-43 (Q4.6, заглушка) и MOS-60 (Q6.5, настоящая форма),
приёмка М-09…М-13, М-16.

Форма ответов — contracts/examples/orders/order-list.json и order.json
(moskollektor-44, 17.09.2026, Q6.1/6.2). Заявка без заказа ТОиР и без прогноза
сегодня не встречается: обе строки заводит одной транзакцией
app.domain.order_rules.завести() — поэтому джойны на work_order, приоритет
и прогноз внутренние, а не LEFT: если инвариант когда-нибудь нарушится, метод
должен явно потерять такую строку, а не молча подставить null в объект,
которого по контракту не бывает.

lead_hours и predicted_failure_at считаются той же формулой, что и при
заведении заявки, — app.domain.order_rules.запас_часов() и момент_отказа().
Две реализации одной величины расходятся молча (см. docstring order_rules.py),
поэтому формула ровно одна.
"""
from fastapi import APIRouter, Depends, HTTPException
import asyncpg

from app.auth.deps import require
from app.db import get_conn
from app.domain.order_rules import запас_часов, момент_отказа

router = APIRouter(prefix="/api")

LIST_SQL = """
SELECT n.id, l.name AS object_name, x.smvu_key, act.name AS work_type_name,
       n.due_at, n.status, p.code AS priority_code, r.as_of, f.horizon_h
  FROM maint.notification n
  JOIN asset.func_location l ON l.id = n.func_location_id
  JOIN ref.object_xref x     ON x.func_location_id = l.id
  JOIN maint.work_order wo   ON wo.notification_id = n.id
  JOIN ref.activity_type act ON act.id = wo.activity_type_id
  JOIN ref.priority p        ON p.id = n.priority_id
  JOIN pred.forecast f       ON f.forecast_id = n.forecast_id
  JOIN pred.run r            ON r.run_id = f.run_id
 ORDER BY n.due_at
"""

DETAIL_SQL = """
SELECT n.id, n.notification_no, n.notification_kind, n.status, n.source_system,
       n.subject, n.reported_at, n.due_at, n.long_text AS reason,
       n.created_at, n.created_by,
       x.section_id, x.smvu_key,
       l.id AS func_location_id, l.code AS func_location_code, l.name AS object_name,
       cr.code AS criticality_code, cr.name AS criticality_name,
       ot.code AS order_type_code, ot.name AS order_type_name,
       act.code AS activity_type_code, act.name AS activity_type_name,
       wo.order_no, wo.status AS work_order_status,
       wo.planned_start, wo.planned_finish,
       p.code AS priority_code, p.name AS priority_name, p.response_hours,
       f.forecast_id, f.run_id, r.as_of, f.direction, f.horizon_h,
       f.probability, f.risk_rank,
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
  JOIN pred.forecast f        ON f.forecast_id = n.forecast_id
  JOIN pred.run r             ON r.run_id = f.run_id
 WHERE n.id = $1
"""


@router.get("/orders")
async def list_orders(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("orders.read")),
):
    rows = await conn.fetch(LIST_SQL)
    return {
        "schema_version": "orders.v1",
        "total": len(rows),
        "items": [
            {
                "id": r["id"],
                "object_name": r["object_name"],
                "smvu_key": r["smvu_key"],
                "work_type_name": r["work_type_name"],
                "due_at": r["due_at"],
                "lead_hours": round(запас_часов(r["due_at"], r["as_of"], r["horizon_h"]), 1),
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

    collector, picket = row["smvu_key"].split(":")
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
        "object": {
            "section_id": row["section_id"],
            "smvu_key": row["smvu_key"],
            "collector": int(collector),
            "picket": int(picket),
            "func_location_id": row["func_location_id"],
            "func_location_code": row["func_location_code"],
            "name": row["object_name"],
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
            "predicted_failure_at": момент_отказа(row["as_of"], row["horizon_h"]),
            "lead_hours": round(запас_часов(row["due_at"], row["as_of"], row["horizon_h"]), 1),
        },
        "reason": row["reason"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }
