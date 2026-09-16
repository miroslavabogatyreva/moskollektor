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
from app.api.orders import router as orders_router
from app.api.routes import router
from app.db import get_pool

app = FastAPI(title="Москоллектор API")
app.include_router(router)
app.include_router(orders_router)
app.include_router(audit_router)


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
    """
    response = await call_next(request)
    if request.url.path == "/health":
        return response

    pool = await get_pool()
    login = request.headers.get("x-user-login")
    async with pool.acquire() as conn:
        user_id = None
        if login:
            user_id = await conn.fetchval(
                "SELECT user_id FROM ref.app_user WHERE login = $1", login,
            )
        await conn.execute(
            """
            INSERT INTO audit.user_action (user_id, method, path, status_code, ip_address)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, request.method, request.url.path, response.status_code,
            request.client.host if request.client else None,
        )
    return response


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
