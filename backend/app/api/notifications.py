"""Уведомления от прогноза: список, живой поток, квитирование. Задача MOS-42
(Q4.5), приёмка Ф-88, Ф-89, Ф-90.

Источник — maint.notification с source_system='forecast': это те же заявки,
которые заводит app.domain.order_rules.завести() (forecast_id обязателен тем же
CHECK, что и там). Отдельной таблицы тревог не заводим и risk_class тут ни при
чём — Ф-90 просит вероятность, горизонт и локацию, а всё это уже лежит у заявки.

GET /api/alerts/stream — SSE (docs/HLD.md разд. 4.3: транспорт, пинг раз
в 20 с, `X-Accel-Buffering: no`). Разбор входа делаем один раз коротким
соединением из пула и сразу отдаём его обратно — connection на весь поток
не держим: 20 открытых вкладок не должны съесть пул asyncpg (get_pool()).
Поэтому FastAPI Depends(require(...)) здесь не годится — Depends с yield
живёт до конца ответа, а для StreamingResponse это конец потока, то есть
соединение простояло бы занятым, пока диспетчер не закроет вкладку. Личность
и право проверяем вызовом тех же get_current_user()/require() напрямую,
как это уже делает _selfcheck() в app.auth.deps.
"""

import asyncio
import json

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.deps import get_current_user, require, видимые_участки, проверить_участок
from app.db import get_conn, get_pool

router = APIRouter(prefix="/api")


# Порядок полей — как в _строка()/list_notifications(): response_model меняет
# сериализацию по объявленному порядку и объявленному типу, а не только
# добавляет схему в /openapi.json. Нашёл 59 (check-api-contract): без модели
# схема ответа у GET пустая — {}. Типы сверены с колонками (docs/HLD.md разд. 5.4):
# probability — real (float4, уже float), horizon_h — smallint (уже int),
# acked_by здесь login из JOIN на ref.app_user, а не число.
class NotificationItem(BaseModel):
    id: int
    reported_at: str
    object_name: str | None
    smvu_key: str | None
    probability: float
    horizon_h: int
    as_of: str | None
    acked_at: str | None
    acked_by: str | None


class NotificationsResponse(BaseModel):
    total: int
    items: list[NotificationItem]

ПИНГ_С = 20  # HLD разд. 4.3: пинг раз в 20 с, иначе nginx рвёт по своему таймауту
ОПРОС_С = 5

# Область видимости (MOS-107) — тот же x.section_id, что и у risks/forecasts/orders.
FROM_SQL = """
  FROM maint.notification n
  JOIN asset.func_location l ON l.id = n.func_location_id
  JOIN ref.object_xref x     ON x.func_location_id = l.id
  JOIN pred.forecast f       ON f.forecast_id = n.forecast_id
  JOIN pred.run r            ON r.run_id = f.run_id
  LEFT JOIN ref.app_user au  ON au.user_id = n.acked_by
 WHERE n.source_system = 'forecast'
   AND ($1::boolean IS NULL OR (n.acked_at IS NOT NULL) = $1)
   AND ($2::int[] IS NULL OR x.section_id = ANY($2))
"""

COUNT_SQL = f"SELECT count(*) {FROM_SQL}"

LIST_SQL = f"""
SELECT n.id, n.reported_at, l.name AS object_name, x.smvu_key,
       f.probability, f.horizon_h, r.as_of,
       n.acked_at, au.login AS acked_by
{FROM_SQL}
 ORDER BY n.reported_at DESC, n.id DESC
 LIMIT $3 OFFSET $4
"""

# Поток отдаёт то же самое, но по id больше последнего показанного (курсор
# Last-Event-ID), без пагинации и без фильтра acked — новая заявка идёт
# в полосу независимо от того, квитирована ли она уже кем-то другим.
# Область видимости — тем же участком, что и у списка: техник в потоке
# не должен увидеть чужой коллектор раньше, чем откроет список.
СОБЫТИЯ_SQL = """
SELECT n.id, n.reported_at, l.name AS object_name, x.smvu_key,
       f.probability, f.horizon_h, r.as_of
  FROM maint.notification n
  JOIN asset.func_location l ON l.id = n.func_location_id
  JOIN ref.object_xref x     ON x.func_location_id = l.id
  JOIN pred.forecast f       ON f.forecast_id = n.forecast_id
  JOIN pred.run r            ON r.run_id = f.run_id
 WHERE n.source_system = 'forecast' AND n.id > $1
   AND ($2::int[] IS NULL OR x.section_id = ANY($2))
 ORDER BY n.id ASC
 LIMIT 100
"""

ПОСЛЕДНИЙ_ID_SQL = (
    "SELECT max(id) FROM maint.notification WHERE source_system = 'forecast'"
)


def _строка(r: asyncpg.Record) -> dict:
    return {
        "id": r["id"],
        "reported_at": r["reported_at"].isoformat(),
        "object_name": r["object_name"],
        "smvu_key": r["smvu_key"],
        "probability": r["probability"],
        "horizon_h": r["horizon_h"],
        "as_of": r["as_of"].isoformat() if r["as_of"] else None,
    }


@router.get("/notifications", response_model=NotificationsResponse)
async def list_notifications(
    acked: bool | None = Query(None, description="квитировано ли; без параметра — все"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("notifications.read")),
):
    участки = await видимые_участки(user, conn)
    total = await conn.fetchval(COUNT_SQL, acked, участки)
    rows = await conn.fetch(LIST_SQL, acked, участки, limit, offset)
    return {
        "total": total,
        "items": [
            {
                **_строка(r),
                "acked_at": r["acked_at"].isoformat() if r["acked_at"] else None,
                "acked_by": r["acked_by"],
            }
            for r in rows
        ],
    }


@router.post("/notifications/{notification_id}/ack")
async def ack(
    notification_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("notifications.ack")),
):
    """Квитирование (Q6.9). Повторный вызов не перезаписывает первую отметку —
    UPDATE … WHERE acked_at IS NULL ловит это атомарно: при гонке двух
    диспетчеров второй просто не находит строку для обновления и получает
    в ответе отметку первого, а не свою.

    Область видимости проверяется ДО UPDATE, тем же порядком, что у
    GET /api/orders/{id} (403 раньше 404 — иначе по разнице кодов техник
    узнал бы, есть ли чужое уведомление).
    """
    найдено = await conn.fetchrow(
        """
        SELECT n.id, x.section_id FROM maint.notification n
          LEFT JOIN ref.object_xref x ON x.func_location_id = n.func_location_id
         WHERE n.id = $1
        """,
        notification_id,
    )
    await проверить_участок(user, conn, найдено["section_id"] if найдено else None)
    if найдено is None:
        raise HTTPException(404, "уведомление не найдено")

    row = await conn.fetchrow(
        """
        UPDATE maint.notification SET acked_by = $1, acked_at = now()
         WHERE id = $2 AND acked_at IS NULL
        RETURNING acked_by, acked_at
        """,
        user["user_id"],
        notification_id,
    )
    if row is None:
        row = await conn.fetchrow(
            "SELECT acked_by, acked_at FROM maint.notification WHERE id = $1",
            notification_id,
        )
    login = None
    if row["acked_by"] is not None:
        login = await conn.fetchval(
            "SELECT login FROM ref.app_user WHERE user_id = $1", row["acked_by"]
        )
    return {
        "id": notification_id,
        "acked_by": login,
        "acked_at": row["acked_at"].isoformat(),
    }


@router.get(
    "/alerts/stream",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def alerts_stream(request: Request, x_user_login: str | None = Header(None)):
    pool = await get_pool()
    checker = require("notifications.read")
    async with pool.acquire() as conn:
        user = await get_current_user(x_user_login=x_user_login, conn=conn)
        await checker(user=user, conn=conn)
        участки = await видимые_участки(user, conn)

        last_event_id = request.headers.get("last-event-id")
        try:
            последний = int(last_event_id) if last_event_id is not None else None
        except ValueError:
            последний = None  # мусор в заголовке — не 500, начинаем как без заголовка
        if последний is None:
            последний = await conn.fetchval(ПОСЛЕДНИЙ_ID_SQL) or 0

    async def события():
        # Опрос — каждые 5 с, всегда. Пинг — не отдельный таймер, а «прошло
        # 20 с тишины»: если данные шли, пинг не нужен вовсе.
        текущий = последний
        молчим_с = asyncio.get_event_loop().time()
        while not await request.is_disconnected():
            async with pool.acquire() as conn:
                rows = await conn.fetch(СОБЫТИЯ_SQL, текущий, участки)
            сейчас = asyncio.get_event_loop().time()
            if rows:
                for r in rows:
                    текущий = r["id"]
                    yield f"id: {r['id']}\ndata: {json.dumps(_строка(r), ensure_ascii=False)}\n\n"
                молчим_с = сейчас
            elif сейчас - молчим_с >= ПИНГ_С:
                yield ": ping\n\n"
                молчим_с = сейчас
            await asyncio.sleep(ОПРОС_С)

    return StreamingResponse(
        события(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
