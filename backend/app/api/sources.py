"""`GET /api/sources` — состояние источников данных. US-26 (MOS-211), Ф-85, Ф-82.

По каждому источнику — когда пришли последние данные, сколько секунд назад и
какая норма. Источник старше нормы — `lagging = true`, экран пишет «отстаёт N мин».

| code    | Время последних данных                          | Норма                          |
|---------|-------------------------------------------------|--------------------------------|
| smvu    | конец последней пачки потока, load.batch api    | 300 с — ТЗ разд. 9, НФ-73      |
| weather | последний успешный забор, ext.weather_hourly    | 3 600 с — забор раз в час      |
| run     | конец последнего успешного прогона, pred.run    | 2 интервала планировщика       |

Норма расчёта — два интервала, а не один: прогон идёт 4–36 с после слота, и
одного интервала впритык хватило бы, чтобы «отставал» каждый второй взгляд.

Отставание считает база (now() − время), а не браузер — тот же довод, что
у /api/data-status: вычитать в поясе браузера мы уже обжигались.

Системы учёта заявок в списке нет: её эмулятора нет (US-19, MOS-63).
"""

import os

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.schemas import IsoDatetime
from app.auth.deps import require
from app.db import get_conn

router = APIRouter(prefix="/api")

ИНТЕРВАЛ_С = int(os.environ.get("SCHEDULER_INTERVAL_MIN", "4")) * 60

ИСТОЧНИКИ = [
    ("smvu", "Поток СМВУ", 300,
     "SELECT max(finished_at) FROM load.batch WHERE tool = 'api'"),
    ("weather", "Метеоданные", 3600,
     "SELECT max(fetched_at) FROM ext.weather_hourly"),
    ("run", "Расчёт прогноза", 2 * ИНТЕРВАЛ_С,
     "SELECT max(finished_at) FROM pred.run WHERE status IN ('done', 'degraded')"),
]


class Source(BaseModel):
    code: str
    name: str
    last_data_at: IsoDatetime | None
    lag_s: int | None
    norm_s: int
    lagging: bool


@router.get("/sources", response_model=list[Source])
async def sources(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("settings.read")),
):
    ответ = []
    for code, name, norm_s, sql in ИСТОЧНИКИ:
        r = await conn.fetchrow(
            f"SELECT t, extract(epoch FROM now() - t)::int AS lag FROM ({sql}) AS s(t)")
        # Данных не было вовсе — это отставание, а не «всё хорошо».
        ответ.append({"code": code, "name": name, "last_data_at": r["t"], "lag_s": r["lag"],
                      "norm_s": norm_s, "lagging": r["lag"] is None or r["lag"] > norm_s})
    return ответ
