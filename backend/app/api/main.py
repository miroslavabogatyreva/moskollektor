"""Точка входа FastAPI. Задача MOS-40 (Q4.3). Запуск: python -m app.api.main
(backend/Dockerfile: CMD ["python", "-m", "app.api.main"]).

Слушает 0.0.0.0:8000: nginx проксирует /api/ на api:8000 без обрезки префикса
(deploy/nginx/nginx.conf), поэтому маршруты в app.api.routes объявлены с префиксом
/api как есть. /health объявлен прямо на app, а не в роутере — живость не должна
зависеть от разрешений и не пишется в audit.user_action (Q4.10).
"""
import json

import uvicorn
from fastapi import FastAPI, Request, Response

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.geo import router as geo_router
from app.api.helpdesk_emu import emu_router as helpdesk_emu_router
from app.api.ingest import router as ingest_router
from app.api.notifications import router as notifications_router
from app.api.permits import router as permits_router
from app.api.objects import router as objects_router
from app.api.orders import router as orders_router
from app.api.routes import router
from app.api.settings import router as settings_router
from app.api.sources import router as sources_router
from app.api.tech_events import router as tech_events_router
from app.api.weather import emu_router
from app.api.weather import router as weather_router
from app.api.xml import to_xml, wants_xml
from app.auth.session import require_secret
from app.db import get_pool

app = FastAPI(title="Москоллектор API")
app.include_router(auth_router)
app.include_router(router)
app.include_router(orders_router)
app.include_router(audit_router)
app.include_router(objects_router)
app.include_router(settings_router)
app.include_router(sources_router)
app.include_router(geo_router)
app.include_router(ingest_router)
app.include_router(notifications_router)
app.include_router(permits_router)
app.include_router(tech_events_router)
app.include_router(weather_router)
app.include_router(emu_router)
app.include_router(helpdesk_emu_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.middleware("http")
async def write_audit_log(request: Request, call_next):
    """MOS-47 (Q4.10): строка в audit.user_action на каждый запрос кроме /health.

    Личность запроса берём из request.state.user_id (Q4.2, MOS-39) — его кладёт
    либо get_current_user (backend/app/auth/deps.py) по куке mk_session или,
    при AUTH_TRUST_HEADER=1, по заголовку X-User-Login, либо сам маршрут входа
    (POST /api/auth/login, до этого куки у запроса ещё нет). До Q4.2 middleware
    читал заголовок напрямую — после входа по паролю это записало бы в журнал
    не того человека (НФ-85), опознаём независимо от исхода маршрута: middleware
    обязан записать и отказ (401), чтобы попытка неопознанного входа тоже
    осталась в журнале, а не выпала из него потому, что Depends прервал цепочку
    раньше.

    MOS-110 (Q4.12): маршрут может положить request.state.audit_details ДО того,
    как отдаст ответ (см. app.api.settings.update_setting) — это тот же ряд,
    второго INSERT под него нет.
    """
    response = await call_next(request)
    # Поток СМВУ и эмуляторы внешних систем — не действия человека (HLD разд. 3.5,
    # MOS-37): эмулятор СМВУ шлёт пачку раз в минуту, это 1 440 строк журнала в сутки.
    if request.url.path == "/health" or request.url.path.startswith(("/api/ingest/", "/emu/")):
        return response

    pool = await get_pool()
    user_id = getattr(request.state, "user_id", None)
    details = getattr(request.state, "audit_details", None)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit.user_action (user_id, method, path, status_code, ip_address, details)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id, request.method, request.url.path, response.status_code,
            request.client.host if request.client else None,
            details,
        )
    return response


@app.middleware("http")
async def convert_to_xml(request: Request, call_next):
    """MOS-44 (Q4.7): XML по `Accept: application/xml` или `?format=xml`,
    приёмка Ф-80. Требование строже плана — XML отдают все GET-методы,
    которые отвечают JSON, а не три перечисленных в docs/plan.md (ОВ-52
    закрыт этим решением, разбор в docs/HLD.md разд. 3.4).

    Добавлен декоратором после write_audit_log, поэтому в стеке middleware
    стоит снаружи от него: аудит логирует настоящий JSON-ответ, здесь только
    переупаковка готовых байт в XML перед отдачей клиенту. Ответы не в JSON
    (поток SSE из MOS-42, HTML `/docs`) распознаются по content-type и уходят
    как есть.
    """
    response = await call_next(request)
    if not wants_xml(request.headers.get("accept", ""), request.query_params.get("format")):
        return response
    # JSON — и application/json, и любой «+json» (application/geo+json у /api/geo/sections, MOS-45).
    ctype = response.headers.get("content-type", "").split(";")[0].strip()
    if ctype != "application/json" and not ctype.endswith("+json"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    xml_body = to_xml(json.loads(body))
    headers = dict(response.headers)
    headers["content-type"] = "application/xml"
    headers.pop("content-length", None)
    return Response(content=xml_body, status_code=response.status_code, headers=headers)


if __name__ == "__main__":
    require_secret()
    uvicorn.run(app, host="0.0.0.0", port=8000)
