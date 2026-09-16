"""Проверка личности и разрешений на каждый метод API. Задача MOS-38 (Q4.1).

Ролей четыре, колонка role_code лежит в ref.app_user (db/migrations/008_rbac.sql).
Какие разрешения даёт роль — не решение кода, а данные в ref.role_permission
(db/seed/rbac.sql): require(permission_code) сверяет запрошенный код со списком
роли пользователя. НФ-43 требует отказа при прямом переходе по URL, а не только
скрытия пункта меню в интерфейсе — значит проверка обязана жить на сервере,
а не во фронте, и её место здесь.

ponytail: пока личность берётся из заголовка X-User-Login без пароля — вход
(LDAP simple bind, локальный пароль argon2, сессия в httpOnly-куке, docs/HLD.md
разд. 3.5) заводит задача Q4.2 (backend/app/auth/ldap.py), она идёт следующей
в очереди блока. get_current_user — тот шов, который 4.2 заменит; остальные
методы API зависят от неё, а не от механизма входа, и переделка 4.2 их не заденет.
"""
import asyncio

import asyncpg
from fastapi import Depends, Header, HTTPException

from app.db import get_conn


async def get_current_user(
    x_user_login: str | None = Header(None),
    conn: asyncpg.Connection = Depends(get_conn),
) -> asyncpg.Record:
    if not x_user_login:
        raise HTTPException(401, "нужен заголовок X-User-Login")
    user = await conn.fetchrow(
        "SELECT user_id, login, role_code FROM ref.app_user WHERE login = $1 AND is_active",
        x_user_login,
    )
    if user is None:
        raise HTTPException(401, "учётная запись не найдена или заблокирована")
    return user


def require(permission_code: str):
    """Depends(require('orders.read')) на методе — 403 без нужного разрешения."""

    async def checker(
        user: asyncpg.Record = Depends(get_current_user),
        conn: asyncpg.Connection = Depends(get_conn),
    ) -> asyncpg.Record:
        allowed = await conn.fetchval(
            "SELECT true FROM ref.role_permission WHERE role_code = $1 AND permission_code = $2",
            user["role_code"], permission_code,
        )
        if not allowed:
            raise HTTPException(403, f"роль {user['role_code']} не даёт разрешения {permission_code}")
        return user

    return checker


def _selfcheck():
    """Логика require() без базы: подставной conn отвечает по двум словарям."""

    class _FakeConn:
        def __init__(self, users, grants):
            self._users = users
            self._grants = grants

        async def fetchrow(self, _sql, login):
            return self._users.get(login)

        async def fetchval(self, _sql, role_code, permission_code):
            return (role_code, permission_code) in self._grants

    async def run():
        conn = _FakeConn(
            users={"disp1": {"user_id": 1, "login": "disp1", "role_code": "dispatcher"}},
            grants={("dispatcher", "risks.read")},
        )

        try:
            await get_current_user(x_user_login=None, conn=conn)
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("должен упасть без заголовка")

        try:
            await get_current_user(x_user_login="ghost", conn=conn)
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("должен упасть на неизвестном логине")

        user = await get_current_user(x_user_login="disp1", conn=conn)
        assert user["role_code"] == "dispatcher"

        allowed = require("risks.read")
        assert (await allowed(user=user, conn=conn))["login"] == "disp1"

        denied = require("audit.read")
        try:
            await denied(user=user, conn=conn)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("должен упасть без разрешения audit.read")

    asyncio.run(run())
    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
