"""Точка входа FastAPI. Задача MOS-40 (Q4.3). Запуск: python -m app.api.main
(backend/Dockerfile: CMD ["python", "-m", "app.api.main"]).

Слушает 0.0.0.0:8000: nginx проксирует /api/ на api:8000 без обрезки префикса
(deploy/nginx/nginx.conf), поэтому маршруты в app.api.routes объявлены с префиксом
/api как есть. /health объявлен прямо на app, а не в роутере — живость не должна
зависеть от разрешений и не пишется в audit.user_action (Q4.10).
"""
import uvicorn
from fastapi import FastAPI, Request

from app.api.audit import router as audit_router
from app.api.notifications import router as notifications_router
from app.api.objects import router as objects_router
from app.api.orders import router as orders_router
from app.api.routes import router
from app.api.settings import router as settings_router
from app.api.tech_events import router as tech_events_router
from app.db import get_pool

app = FastAPI(title="Москоллектор API")
app.include_router(router)
app.include_router(orders_router)
app.include_router(audit_router)
app.include_router(objects_router)
app.include_router(settings_router)
app.include_router(notifications_router)
app.include_router(tech_events_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.middleware("http")
async def write_audit_log(request: Request, call_next):
    """MOS-47 (Q4.10): строка в audit.user_action на каждый запрос кроме /health.

    Личность запроса та же, что видит get_current_user (заголовок X-User-Login),
    но опознаём независимо от него: middleware обязан записать и отказ (401),
    чтобы попытка неопознанного входа тоже осталась в журнале, а не выпала
    из него потому, что Depends в маршруте прервал цепочку раньше.

    MOS-110 (Q4.12): маршрут может положить request.state.audit_details ДО того,
    как отдаст ответ (см. app.api.settings.update_setting) — это тот же ряд,
    второго INSERT под него нет.
    """
    response = await call_next(request)
    if request.url.path == "/health":
        return response

    pool = await get_pool()
    login = request.headers.get("x-user-login")
    details = getattr(request.state, "audit_details", None)
    async with pool.acquire() as conn:
        user_id = None
        if login:
            user_id = await conn.fetchval(
                "SELECT user_id FROM ref.app_user WHERE login = $1", login,
            )
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
