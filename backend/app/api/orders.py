"""Заявки. Задачи MOS-43 (Q4.6, заглушка) и MOS-60 (Q6.5, настоящая форма),
приёмка М-09…М-13, М-16.

Форма ответов — contracts/examples/orders/order-list.json и order.json
(moskollektor-44, 17.09.2026, Q6.1/6.2). Заявка без заказа ТОиР и без прогноза
сегодня не встречается: обе строки заводит одной транзакцией
app.domain.order_rules.завести() — поэтому джойны на work_order, приоритет
и прогноз внутренние, а не LEFT: если инвариант когда-нибудь нарушится, метод
должен явно потерять такую строку, а не молча подставить null в объект,
которого по контракту не бывает.

Срок заявки отдаётся двумя числами рядом (MOS-179): deadline_hours — due_at − reported_at
самой заявки, из её же двух колонок, и priority.response_hours — норматив приоритета
из справочника. У новых заявок класса A они совпадают (16 ч); у B и C срок меньше
норматива, его ограничил потолок превентивности order_preventive_cap_h; у заведённых
до миграции 037 они расходятся, потому что их срок считался от as_of + horizon_h, а историю
задним числом мы не переписываем. Расхождение видно в одной строке карточки.

predicted_failure_at и lead_hours убраны: момент as_of + horizon_h — конец окна риска,
а не предсказанный отказ (ревью Codex 22.09.2026, М-13). Вместо них — warning_opened_at
и risk_window_end, колонки заявки из той же миграции.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.api.schemas import OrderDetail, OrderList
from app.auth.deps import require, видимые_участки, проверить_участок
from app.db import get_conn

router = APIRouter(prefix="/api")

FROM_SQL = """
  FROM maint.notification n
  JOIN asset.func_location l ON l.id = n.func_location_id
  JOIN ref.object_xref x     ON x.func_location_id = l.id
  JOIN maint.work_order wo   ON wo.notification_id = n.id
  JOIN ref.activity_type act ON act.id = wo.activity_type_id
  JOIN ref.priority p        ON p.id = n.priority_id
  JOIN pred.forecast f       ON f.forecast_id = n.forecast_id
  JOIN pred.run r            ON r.run_id = f.run_id
 WHERE ($1::int[] IS NULL OR x.section_id = ANY($1))
   AND ($2::date IS NULL OR n.due_at >= timezone('Europe/Moscow', $2::date::timestamp))
   AND ($3::date IS NULL OR n.due_at < timezone('Europe/Moscow', $3::date::timestamp))
   AND ($4::text IS NULL OR n.id::text = $4
        OR n.notification_no ILIKE '%' || $4 || '%' OR wo.order_no ILIKE '%' || $4 || '%')
"""

COUNT_SQL = f"SELECT count(*) {FROM_SQL}"

# count(*) OVER() жил бы внутри строк LIST_SQL, а за последней страницей строк
# нет — total подставлялся бы нулём вместо настоящего числа заявок (нашла 28,
# 21.09.2026: GET /api/orders?offset=1000 отвечал total=0 при 224 заявках).
# Отдельный COUNT_SQL от страницы не зависит, как и у /api/forecasts.
# Внутри одного срока выше более срочный приоритет (p.code '1' — аварийный):
# у автозаявок срок часто общий, и 337 «высоких» шли вперемешку с 23 «средними»
# (MOS-130, стенд 28.09.2026).
LIST_SQL = f"""
SELECT n.id, l.name AS object_name, x.smvu_key, act.name AS work_type_name,
       n.due_at, n.reported_at, n.status, p.code AS priority_code
{FROM_SQL}
 ORDER BY n.due_at, p.code, n.id, wo.id
 LIMIT $5 OFFSET $6
"""

DETAIL_SQL = """
SELECT n.id, n.notification_no, n.notification_kind, n.status, n.source_system,
       n.subject, n.reported_at, n.due_at, n.long_text AS reason,
       n.warning_opened_at, n.risk_window_end,
       n.external_status, n.external_status_at, n.external_assignee,
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


def _часов(от, до) -> float:
    """Срок заявки в часах: due_at − reported_at, из двух колонок самой заявки."""
    return round((до - от).total_seconds() / 3600, 1)


@router.get("/orders", response_model=OrderList)
async def list_orders(
    limit: int = Query(200, ge=1, le=1000, description="сколько записей вернуть, потолок 1000"),
    offset: int = Query(0, ge=0, description="сколько записей пропустить от начала выборки"),
    due_from: date | None = Query(None, description="срок с этой даты (МСК), включительно"),
    due_to: date | None = Query(None, description="срок по эту дату (МСК), весь день целиком"),
    q: str | None = Query(
        None,
        max_length=40,
        description="номер заявки: id целиком или часть номера уведомления (AF…) или заказа (AW…)",
    ),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("orders.read")),
):
    """Период срока — US-18 сц. 2, план профилактики на неделю. due_from/due_to —
    московские даты, обе границы включительны: верхняя граница в запросе — начало
    СЛЕДУЮЩЕГО за due_to дня, как у GET /api/forecasts. Период стоит в общем
    FROM_SQL, поэтому total и страница считают одни и те же заявки, и число строк
    экрана совпадает с total ответа. q — поиск по номеру заявки: id целиком
    или часть номера уведомления (AF…) и заказа (AW…), без учёта регистра."""
    участки = await видимые_участки(user, conn)
    до = due_to + timedelta(days=1) if due_to else None
    q = (q or "").strip() or None
    total = await conn.fetchval(COUNT_SQL, участки, due_from, до, q)
    rows = await conn.fetch(LIST_SQL, участки, due_from, до, q, limit, offset)
    return {
        "schema_version": "orders.v1",
        "total": total,
        "items": [
            {
                "id": r["id"],
                "object_name": r["object_name"],
                "smvu_key": r["smvu_key"],
                "work_type_name": r["work_type_name"],
                "due_at": r["due_at"],
                "deadline_hours": _часов(r["reported_at"], r["due_at"]),
                "status": r["status"],
                "priority_code": r["priority_code"],
            }
            for r in rows
        ],
    }

HISTORY_SQL = """
SELECT status, assignee, changed_at, source
  FROM maint.notification_status_log
 WHERE notification_id = $1
 ORDER BY changed_at, id
"""


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(
    order_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("orders.read")),
):
    row = await conn.fetchrow(DETAIL_SQL, order_id)
    await проверить_участок(user, conn, row["section_id"] if row else None)
    if row is None:
        raise HTTPException(404, "заявка не найдена")

    # История заявки: смены статуса с источником (миграция 058, US-19, Ф-87).
    history = await conn.fetch(HISTORY_SQL, order_id)
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
        "deadline_hours": _часов(row["reported_at"], row["due_at"]),
        "warning_opened_at": row["warning_opened_at"],
        "risk_window_end": row["risk_window_end"],
        "external_status": row["external_status"],
        "external_status_at": row["external_status_at"],
        "external_assignee": row["external_assignee"],
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
        },
        "reason": row["reason"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "status_history": [dict(h) for h in history],
    }
