"""`POST /api/ingest/readings` — приём потока показаний СМВУ. Задача MOS-37 (Q3.7), Ф-82.

Шлёт сюда внешняя система, а не человек, поэтому вход по куке не годится: метод
закрыт токеном из INGEST_TOKEN (заголовок `Authorization: Bearer …`). Токен не
задан или короче 32 символов — 503: приём выключен, а не открыт всем. Сравнение —
hmac.compare_digest, чтобы время ответа не выдавало токен по символу.

В audit.user_action запрос не пишется (app.api.main): HLD разд. 3.5 держит аудит
на действиях человека, а не на потоке событий СМВУ.
"""

import hmac
import os

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from app.auth.deps import get_current_user, require
from app.db import get_conn
from app.ingest.readings import МАКС_ПАЧКА, принять

router = APIRouter(prefix="/api/ingest")


class Reading(BaseModel):
    journal_id: int
    channel_id: int
    read_time: AwareDatetime   # без пояса не берём: чей это час, догадываться нельзя
    is_alarm: bool
    value: str | None


class ReadingsIn(BaseModel):
    readings: list[Reading] = Field(min_length=1, max_length=МАКС_ПАЧКА)


class ReadingsOut(BaseModel):
    batch_id: int
    received: int
    accepted: int
    unknown_channel: int
    duplicates: int


def проверить_токен(authorization: str | None) -> None:
    токен = os.environ.get("INGEST_TOKEN", "")
    if len(токен) < 32:
        raise HTTPException(503, "приём потока выключен: INGEST_TOKEN не задан")
    # Байты, а не строки: compare_digest на строке с не-ASCII символом бросает
    # TypeError, и чужой заголовок давал 500 вместо 401 (нашёл test_ods_ingest).
    if not authorization or not hmac.compare_digest(
        authorization.encode(), f"Bearer {токен}".encode()
    ):
        raise HTTPException(401, "нужен заголовок Authorization: Bearer <INGEST_TOKEN>")


@router.post("/readings", response_model=ReadingsOut)
async def ingest_readings(
    body: ReadingsIn,
    authorization: str | None = Header(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    проверить_токен(authorization)
    return await принять(conn, [r.model_dump() for r in body.readings])


# ── События журнала ОДС (US-12 сц. 5, Ф-61, миграция 054) ────────────────────

class OdsEvent(BaseModel):
    source_id: str = Field(min_length=1, max_length=100)   # ключ события в системе ОДС
    event_time: AwareDatetime
    section_id: int | None = None
    event_text: str = Field(min_length=1, max_length=2000)
    event_type: str = Field(pattern="^(Предупреждение|Норма)$")


class OdsEventsIn(BaseModel):
    events: list[OdsEvent] = Field(min_length=1, max_length=1000)


class OdsEventsOut(BaseModel):
    received: int
    accepted: int
    duplicates: int
    last_id: int | None


@router.post("/ods-events", response_model=OdsEventsOut, status_code=201)
async def ingest_ods_events(
    body: OdsEventsIn,
    request: Request,
    authorization: str | None = Header(None),
    x_user_login: str | None = Header(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Приём событий журнала ОДС от эмулятора (US-12 сц. 5, Ф-61).

    Интеграции с настоящим журналом ОДС нет и не будет (ответ 10, сводный 9),
    поэтому писать сюда может эмулятор двумя способами: внешняя система —
    с токеном INGEST_TOKEN, как поток СМВУ; администратор — под правом
    ods_events.write (так эмулятором выступает E2E-тест). Повтор source_id
    не заводит дубль. Журнал технологических событий показывает событие
    через GET /api/tech-events, а экран узнаёт о нём по GET /api/tech-events/ods-last.
    """
    if authorization:
        проверить_токен(authorization)
        кто = "ingest-token"
    else:
        user = await get_current_user(
            request=request, mk_session=request.cookies.get("mk_session"),
            x_user_login=x_user_login, conn=conn)
        await require("ods_events.write")(user=user, conn=conn)
        кто = user["login"]

    участки = {e.section_id for e in body.events if e.section_id is not None}
    if участки:
        известные = {r["section_id"] for r in await conn.fetch(
            "SELECT section_id FROM ref.object_xref WHERE section_id = ANY($1::int[])",
            list(участки))}
        if чужие := sorted(участки - известные):
            raise HTTPException(422, f"участков {чужие} нет в ref.object_xref")

    принято = 0
    for e in body.events:
        принято += int((await conn.execute(
            """
            INSERT INTO maint.ods_event (source_id, event_time, section_id, event_text,
                                         event_type, received_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (source_id) DO NOTHING
            """,
            e.source_id, e.event_time, e.section_id, e.event_text, e.event_type, кто,
        )).split()[-1])
    return {
        "received": len(body.events),
        "accepted": принято,
        "duplicates": len(body.events) - принято,
        "last_id": await conn.fetchval("SELECT max(event_id) FROM maint.ods_event"),
    }
