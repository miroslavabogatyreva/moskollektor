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
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, Field

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
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {токен}"):
        raise HTTPException(401, "нужен заголовок Authorization: Bearer <INGEST_TOKEN>")


@router.post("/readings", response_model=ReadingsOut)
async def ingest_readings(
    body: ReadingsIn,
    authorization: str | None = Header(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    проверить_токен(authorization)
    return await принять(conn, [r.model_dump() for r in body.readings])
