"""Проверка личности и разрешений на каждый метод API. Задача MOS-38 (Q4.1).

Роли — четыре роли заказчика (MOS-107, Q4.11): dispatcher, ods_dispatcher,
technician, admin. Роли складываются: они лежат в ref.user_role
(db/migrations/044_roles_scope.sql), у человека их может быть несколько.
Какие разрешения даёт роль — не решение кода, а данные в ref.role_permission
(db/seed/rbac.sql): require(permission_code) пускает, если код даёт хотя бы
одна роль пользователя.

Область видимости — видимые_участки(): ods_dispatcher и admin видят всё,
остальные — участки коллекторов из своих узлов ref.user_scope. Участок относится
к коллектору запросом УЧАСТКИ_КОЛЛЕКТОРА из расчёта, второй копии правила нет. НФ-43 требует отказа при прямом переходе по URL, а не только
скрытия пункта меню в интерфейсе — значит проверка обязана жить на сервере,
а не во фронте, и её место здесь.

Личность определяется здесь и только здесь (Q4.2, MOS-39): сперва кука
mk_session (backend/app/auth/session.py, подпись HMAC-SHA256), заголовок
X-User-Login без пароля принимается лишь при AUTH_TRUST_HEADER=1 — заглушка
Q4.1 держится на нём же (19 файлов проверок), но на приёмке эксперта переменная
стоит 0, и подделка заголовка отвечает 401. get_current_user кладёт user_id
в request.state — middleware аудита (app.api.main) читает оттуда, а не
из заголовка, иначе после входа по паролю журнал записал бы не того человека
(НФ-85).
"""
import asyncio
import os

import asyncpg
from fastapi import Cookie, Depends, Header, HTTPException, Request

from app.auth.session import TTL_LOCAL_S, sign_session, verify_session
from app.db import get_conn
from app.worker.run_v3 import УЧАСТКИ_КОЛЛЕКТОРА

ВИДЯТ_ВСЁ = {"ods_dispatcher", "admin"}

# Узел уровня 2 — сам коллектор, уровня 1 — коллекторы под ним (parent_id).
ВИДИМЫЕ_УЧАСТКИ_SQL = f"""
WITH u AS ({УЧАСТКИ_КОЛЛЕКТОРА})
SELECT u.section_id
  FROM u
  JOIN smvu.object_tree t ON t.object_id = u.collector_id
 WHERE EXISTS (SELECT 1 FROM ref.user_scope s
                WHERE s.login = $1 AND s.object_id IN (t.object_id, t.parent_id))
"""


async def get_current_user(
    request: Request,
    mk_session: str | None = Cookie(None),
    x_user_login: str | None = Header(None),
    conn: asyncpg.Connection = Depends(get_conn),
) -> asyncpg.Record:
    login = verify_session(mk_session) if mk_session else None
    if login is None and os.environ.get("AUTH_TRUST_HEADER") == "1" and x_user_login:
        login = x_user_login
    if login is None:
        raise HTTPException(401, "нужен вход")
    user = await conn.fetchrow(
        """
        SELECT u.user_id, u.login, u.full_name, u.auth_source,
               ARRAY(SELECT r.role_code FROM ref.user_role r
                      WHERE r.login = u.login ORDER BY r.role_code) AS roles
          FROM ref.app_user u
         WHERE u.login = $1 AND u.is_active
        """,
        login,
    )
    if user is None:
        raise HTTPException(401, "учётная запись не найдена или заблокирована")
    request.state.user_id = user["user_id"]
    return user


def require(permission_code: str):
    """Depends(require('orders.read')) на методе — 403 без нужного разрешения."""

    async def checker(
        user: asyncpg.Record = Depends(get_current_user),
        conn: asyncpg.Connection = Depends(get_conn),
    ) -> asyncpg.Record:
        allowed = await conn.fetchval(
            "SELECT true FROM ref.role_permission"
            " WHERE role_code = ANY($1::text[]) AND permission_code = $2 LIMIT 1",
            list(user["roles"]), permission_code,
        )
        if not allowed:
            роли = ", ".join(user["roles"]) or "нет ролей"
            raise HTTPException(403, f"роли ({роли}) не дают разрешения {permission_code}")
        return user

    return checker


async def видимые_участки(user, conn) -> list[int] | None:
    """Участки, которые видит пользователь. None — видит все (Ф-66, НФ-43).

    Список отдаётся в SQL параметром `$n::int[]` с условием
    `($n IS NULL OR section_id = ANY($n))`, чтобы total и страница считались
    по той же подрезке, что и строки.
    """
    if ВИДЯТ_ВСЁ & set(user["roles"]):
        return None
    return [r["section_id"] for r in await conn.fetch(ВИДИМЫЕ_УЧАСТКИ_SQL, user["login"])]


async def проверить_участок(user, conn, section_id: int | None) -> None:
    """403 на участок вне области видимости: карточка, прогноз, заявка.

    Вызывать ДО ответа 404. section_id=None — объекта нет: тому, кто видит
    не весь парк, это тоже 403, иначе по разнице 404 и 403 он узнал бы,
    существует ли чужой объект. Тому, кто видит всё, функция молчит,
    и 404 отвечает вызывающий.
    """
    участки = await видимые_участки(user, conn)
    if участки is not None and (section_id is None or section_id not in участки):
        raise HTTPException(403, f"объект вне области видимости {user['login']}")


def _selfcheck():
    """Логика require(), области видимости и входа без базы: подставной conn."""
    os.environ.setdefault("AUTH_SECRET", "x" * 32)  # sign_session/verify_session читают лениво

    class _FakeConn:
        def __init__(self, users, grants, scope):
            self._users = users
            self._grants = grants
            self._scope = scope  # login -> участки, которые вернул бы ВИДИМЫЕ_УЧАСТКИ_SQL

        async def fetchrow(self, _sql, login):
            return self._users.get(login)

        async def fetchval(self, _sql, roles, permission_code):
            return any((r, permission_code) in self._grants for r in roles)

        async def fetch(self, _sql, login):
            return [{"section_id": s} for s in self._scope.get(login, [])]

    class _FakeRequest:
        """У request.state в FastAPI нет схемы — любой атрибут пишется на лету."""

        def __init__(self):
            self.state = type("_State", (), {})()

    def u(login, *roles):
        return {"user_id": 1, "login": login, "roles": list(roles)}

    async def run():
        conn = _FakeConn(
            users={"disp1": u("disp1", "dispatcher")},
            grants={("dispatcher", "risks.read"), ("admin", "audit.read")},
            scope={"tech1": [10, 11], "tech2": [10, 11, 20], "disp1": [10, 11, 12, 20, 21]},
        )

        try:
            await get_current_user(
                request=_FakeRequest(), mk_session=None, x_user_login=None, conn=conn
            )
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("должен упасть без куки")

        # НФ-43: AUTH_TRUST_HEADER=0 (значение на приёмке) — заголовок X-User-Login
        # не пускает, даже с существующим логином. Подделка заголовка отвечает 401.
        os.environ.pop("AUTH_TRUST_HEADER", None)
        try:
            await get_current_user(
                request=_FakeRequest(), mk_session=None, x_user_login="disp1", conn=conn
            )
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("AUTH_TRUST_HEADER=0 не должен пускать по заголовку")

        # AUTH_TRUST_HEADER=1 — путь по заголовку жив для старых проверок (19 файлов).
        os.environ["AUTH_TRUST_HEADER"] = "1"
        try:
            req = _FakeRequest()
            user = await get_current_user(
                request=req, mk_session=None, x_user_login="disp1", conn=conn
            )
            assert user["login"] == "disp1"
            assert req.state.user_id == user["user_id"]

            try:
                await get_current_user(
                    request=_FakeRequest(), mk_session=None, x_user_login="ghost", conn=conn
                )
            except HTTPException as e:
                assert e.status_code == 401
            else:
                raise AssertionError("должен упасть на неизвестном логине")
        finally:
            os.environ.pop("AUTH_TRUST_HEADER", None)

        # Кука — основной путь, работает и при AUTH_TRUST_HEADER=0.
        token = sign_session("disp1", TTL_LOCAL_S)
        req = _FakeRequest()
        user = await get_current_user(request=req, mk_session=token, x_user_login=None, conn=conn)
        assert user["roles"] == ["dispatcher"]
        assert req.state.user_id == user["user_id"]

        # Подделанная подпись куки — 401 (последний символ подписи испорчен).
        forged = token[:-1] + ("0" if token[-1] != "0" else "1")
        try:
            await get_current_user(
                request=_FakeRequest(), mk_session=forged, x_user_login=None, conn=conn
            )
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("подделанная подпись куки должна дать 401")

        allowed = require("risks.read")
        assert (await allowed(user=user, conn=conn))["login"] == "disp1"

        denied = require("audit.read")
        try:
            await denied(user=user, conn=conn)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("должен упасть без разрешения audit.read")

        # Роли складываются: dispatcher + admin получает audit.read от admin.
        both = u("both", "dispatcher", "admin")
        assert (await denied(user=both, conn=conn))["login"] == "both"

        # Без ролей — 403, а не падение на пустом списке.
        try:
            await allowed(user=u("nobody"), conn=conn)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("без ролей должен получить 403")

        # ods_dispatcher и admin видят всё — None, базу не спрашивают.
        assert await видимые_участки(u("ods1", "ods_dispatcher"), conn) is None
        assert await видимые_участки(u("x", "technician", "admin"), conn) is None

        # Техник — только свои участки; чужой — 403, свой проходит.
        tech = u("tech1", "technician")
        assert await видимые_участки(tech, conn) == [10, 11]
        await проверить_участок(tech, conn, 10)
        try:
            await проверить_участок(tech, conn, 12)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("чужой участок должен дать 403")

        # Два узла у одного техника: объединение, строго между tech1 и всем парком.
        # Само объединение делает SQL (EXISTS по ref.user_scope) — здесь проверяется
        # только проводка; на живой базе его меряет check_scope в delivery/check-all.sh
        # (tech2: 79 + 9 = 88 при 3 173).
        tech2 = await видимые_участки(u("tech2", "technician"), conn)
        assert set(await видимые_участки(tech, conn)) < set(tech2) < set(await видимые_участки(u("disp1", "dispatcher"), conn))
        await проверить_участок(u("tech2", "technician"), conn, 20)

        # Несуществующий объект: технику 403, как чужой; видящему всё — молчание (404 даст метод).
        try:
            await проверить_участок(tech, conn, None)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("несуществующий объект у техника должен дать 403, а не 404")
        await проверить_участок(u("ods1", "ods_dispatcher"), conn, None)

        # dispatcher и technician без строк в ref.user_scope видят пусто, а не всё.
        assert await видимые_участки(u("lost", "dispatcher"), conn) == []
        assert await видимые_участки(u("lost", "technician"), conn) == []

    asyncio.run(run())
    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
