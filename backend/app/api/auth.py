"""Вход по логину и паролю, LDAP-каталог, служебные методы. Задача MOS-39
(Q4.2), приёмка Ф-66, НФ-43, НФ-76, НФ-85. GET/PATCH /users — MOS-226
(Q4.19): администратор блокирует и разблокирует пользователя из интерфейса,
без обращения к базе данных (решение Славы 24.09.2026 закрывать Ф-66 кнопкой,
не блокировкой в каталоге). Каталожную учётку блокировка тоже держит:
_sync_ldap_user при входе не трогает is_active, поэтому она переживает
повторный вход через LDAP.

Маршрут входа сам решает способ по auth_source учётки: local — argon2 против
ref.app_user.password_hash, ldap (или неизвестный логин при настроенном
LDAP_URI) — bind в каталог и синхронизация ролей/области видимости из
ref.ldap_role_map (backend/app/auth/ldap.py). Демо-учётки (disp2, ods1,
tech1, admin1) и одноимённые учётки каталога (ldap_dispatcher1, …) — разные
пары «логин/секрет», решение Славы 24.09.2026: демо обязано работать без
каталога.

401 не различает «нет такого логина» и «неверный пароль» (договор API) —
один текст на оба случая, чтобы перебор логинов не давал разведки. Срок
куки mk_session короче у LDAP-сессии (1 ч) — учётку каталога администратор
AD может заблокировать в любой момент, у local (8 ч) это единственный запасной
вход, чаще перелогиниваться незачем.

Блокирующие вызовы (LDAP-сокет, argon2 — память 64 МБ на хеш) идут через
`run_in_threadpool` (находка проверяющей 92, 24.09.2026): без этого один
запрос входа держит весь event loop event до 3 секунд на попытку, пока
каталог не ответит, и весь api стоит для всех остальных запросов разом.
"""
import os

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.schemas import UserItem
from app.auth import ldap as ldap_auth
from app.auth.deps import get_current_user, require
from app.auth.password import verify_password
from app.auth.session import TTL_LDAP_S, TTL_LOCAL_S, sign_session, verify_session
from app.db import get_conn

router = APIRouter(prefix="/api/auth")

_WRONG = "неверный логин или пароль"

# Пароли демо-учёток — здесь и только здесь; db/seed/rbac.sql хранит их argon2-хеши.
# Самопроверка ниже сверяет константу с сидом, чтобы подсказка на экране входа
# не разошлась с базой молча (задание A, п. 8).
DEMO_ACCOUNTS = [
    # ods1 первым: главная — дэш диспетчера ОДС, демо начинается с него (Слава, 28.09.2026).
    # Диспетчер района — disp2, а не dispatcher1: район в выгрузке один (узел 5773),
    # и dispatcher1 с ним видит весь парк, как ods1, — на экране входа роли
    # не отличались бы ничем. disp2 — «район А», коллекторы 5 и 7, 512 участков
    # из 3 173 (US-16). dispatcher1 остаётся учёткой проверок, которым нужен весь парк.
    {"login": "ods1", "password": "ods123456", "role_code": "ods_dispatcher",
     "role_name": "диспетчер ОДС", "sees": "риски, прогнозы, объекты и заявки по всему парку"},
    {"login": "disp2", "password": "disp2123123", "role_code": "dispatcher",
     "role_name": "диспетчер", "sees": "риски, прогнозы, объекты и заявки своего района: коллекторы Альфа и Гамма"},
    {"login": "tech1", "password": "tech123456", "role_code": "technician",
     "role_name": "техник", "sees": "риски, прогнозы, объекты и заявки своего комплекса"},
    {"login": "admin1", "password": "admin123456", "role_code": "admin",
     "role_name": "администратор ИС", "sees": "настройки, журнал аудита и службу каталогов"},
]


class LoginBody(BaseModel):
    login: str
    password: str


class AuthUser(BaseModel):
    login: str
    full_name: str
    roles: list[str]
    auth_source: str


class DemoAccount(BaseModel):
    login: str
    password: str
    role_code: str
    role_name: str
    sees: str


class AuthInfo(BaseModel):
    ldap_configured: bool
    demo_accounts: list[DemoAccount]


class RoleMapItem(BaseModel):
    group_cn: str
    role_code: str | None
    object_id: int | None


class DirectoryInfo(BaseModel):
    uri: str
    base_dn: str
    user_template: str
    role_map: list[RoleMapItem]


class DirectoryCheck(BaseModel):
    ok: bool
    ms: int
    message: str


class UserUpdate(BaseModel):
    is_active: bool


def _deny(request: Request, login_attempt: str) -> HTTPException:
    """401 с текстом договора; введённый логин остаётся в audit.user_action.details
    (без пароля), чтобы попытка входа осталась в журнале, кто бы её ни завершил."""
    request.state.audit_details = {"login_attempt": login_attempt}
    return HTTPException(401, _WRONG)


async def _load_user(conn: asyncpg.Connection, login: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT u.user_id, u.login, u.full_name, u.auth_source, u.password_hash, u.is_active,
               ARRAY(SELECT r.role_code FROM ref.user_role r
                      WHERE r.login = u.login ORDER BY r.role_code) AS roles
          FROM ref.app_user u
         WHERE u.login = $1
        """,
        login,
    )


async def _sync_ldap_user(conn: asyncpg.Connection, login: str, roles: list[str], scope: list[int]) -> None:
    """Заводит/обновляет ref.app_user при входе через каталог и пересобирает
    роли и область видимости заново — так блокировка «убрать из групп» тоже
    действует немедленно, без отдельного признака в базе.

    Одной транзакцией (находка 92, необязательная, но дешёвая): без неё сбой
    между DELETE и INSERT ролей оставил бы человека без единой роли до
    следующего успешного входа."""
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO ref.app_user (login, full_name, auth_source, password_hash)
            VALUES ($1, $1, 'ldap', NULL)
            ON CONFLICT (login) DO UPDATE SET auth_source = 'ldap', password_hash = NULL
            """,
            login,
        )
        await conn.execute("DELETE FROM ref.user_role WHERE login = $1", login)
        if roles:
            await conn.executemany(
                "INSERT INTO ref.user_role (login, role_code) VALUES ($1, $2)",
                [(login, r) for r in roles],
            )
        await conn.execute("DELETE FROM ref.user_scope WHERE login = $1", login)
        if scope:
            await conn.executemany(
                "INSERT INTO ref.user_scope (login, object_id) VALUES ($1, $2)",
                [(login, s) for s in scope],
            )


async def _login_core(body: LoginBody, request: Request, conn: asyncpg.Connection) -> tuple[asyncpg.Record, int]:
    """Вся логика решения — отдельно от set_cookie, чтобы её мог прогнать selfcheck
    без реального HTTP-ответа. Возвращает строку пользователя и ttl куки."""
    user = await _load_user(conn, body.login)
    if user is not None and not user["is_active"]:
        raise _deny(request, body.login)

    auth_source = user["auth_source"] if user is not None else "ldap"

    if auth_source == "local":
        if user is None or user["password_hash"] is None:
            raise _deny(request, body.login)
        # argon2 — память 64 МБ на проверку, блокирующий вызов вне event loop.
        password_ok = await run_in_threadpool(verify_password, user["password_hash"], body.password)
        if not password_ok:
            raise _deny(request, body.login)
        if not user["roles"]:
            raise _deny(request, body.login)
        return user, TTL_LOCAL_S

    if not os.environ.get("LDAP_URI"):
        raise _deny(request, body.login)
    try:
        # bind и поиск групп — сокет, тоже вне event loop (см. докстринг модуля).
        group_cns = await run_in_threadpool(ldap_auth.authenticate, body.login, body.password)
    except ldap_auth.DirectoryUnavailable:
        raise _deny(request, body.login)
    if group_cns is None:
        raise _deny(request, body.login)
    role_map_rows = await conn.fetch("SELECT group_cn, role_code, object_id FROM ref.ldap_role_map")
    roles, scope = ldap_auth.map_groups_to_roles(
        group_cns, [(r["group_cn"], r["role_code"], r["object_id"]) for r in role_map_rows]
    )
    if not roles:
        raise _deny(request, body.login)
    await _sync_ldap_user(conn, body.login, roles, scope)
    user = await _load_user(conn, body.login)
    return user, TTL_LDAP_S


def _user_item(row: asyncpg.Record) -> dict:
    return {
        "login": row["login"],
        "full_name": row["full_name"],
        "auth_source": row["auth_source"],
        "is_active": row["is_active"],
        "roles": list(row["roles"]),
        # US-24 сц. 3: признак, а не хеш — у учётки каталога его нет, пароль проверяет LDAP.
        "has_password": row["password_hash"] is not None,
    }


async def _patch_user_core(
    login: str, body: UserUpdate, request: Request, conn: asyncpg.Connection, current_login: str
) -> asyncpg.Record:
    """Логика PATCH отдельно от Depends — тот же приём, что у _login_core, чтобы
    selfcheck прогонял 400/404/аудит без реального HTTP-ответа (MOS-226, Ф-66)."""
    target = await _load_user(conn, login)
    if target is None:
        raise HTTPException(404, f"пользователя «{login}» нет")
    if login == current_login and not body.is_active:
        raise HTTPException(400, "нельзя заблокировать себя")
    old = target["is_active"]
    await conn.execute(
        "UPDATE ref.app_user SET is_active = $1 WHERE login = $2", body.is_active, login
    )
    request.state.audit_details = {"login": login, "old": old, "new": body.is_active}
    return await _load_user(conn, login)


@router.post("/login", response_model=AuthUser)
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    conn: asyncpg.Connection = Depends(get_conn),
):
    user, ttl = await _login_core(body, request, conn)
    token = sign_session(user["login"], ttl)
    response.set_cookie(
        "mk_session", token, max_age=ttl, httponly=True, secure=True, samesite="lax", path="/",
    )
    # Не через get_current_user (кука ещё не пришла с этим запросом): маршрут
    # входа сам кладёт вошедшего в request.state, иначе журнал не узнал бы,
    # кто прошёл (НФ-85).
    request.state.user_id = user["user_id"]
    return {
        "login": user["login"],
        "full_name": user["full_name"],
        "roles": list(user["roles"]),
        "auth_source": user["auth_source"],
    }


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    mk_session: str | None = Cookie(None),
    conn: asyncpg.Connection = Depends(get_conn),
):
    response.delete_cookie("mk_session", path="/")
    # Необязательно (находка 92), но дёшево: без этого журнал видел бы выход
    # как анонимный запрос, хотя кука ещё называла вышедшего.
    login = verify_session(mk_session) if mk_session else None
    if login is not None:
        request.state.user_id = await conn.fetchval(
            "SELECT user_id FROM ref.app_user WHERE login = $1", login
        )


@router.get("/me", response_model=AuthUser)
async def me(user: asyncpg.Record = Depends(get_current_user)):
    return {
        "login": user["login"],
        "full_name": user["full_name"],
        "roles": list(user["roles"]),
        "auth_source": user["auth_source"],
    }


@router.get("/info", response_model=AuthInfo)
async def info():
    demo = DEMO_ACCOUNTS if os.environ.get("AUTH_DEMO_HINTS") == "1" else []
    return {"ldap_configured": bool(os.environ.get("LDAP_URI")), "demo_accounts": demo}


@router.get("/directory", response_model=DirectoryInfo)
async def directory(
    conn: asyncpg.Connection = Depends(get_conn),
    _user: asyncpg.Record = Depends(require("settings.read")),
):
    rows = await conn.fetch(
        "SELECT group_cn, role_code, object_id FROM ref.ldap_role_map ORDER BY group_cn"
    )
    return {
        "uri": os.environ.get("LDAP_URI", ""),
        "base_dn": os.environ.get("LDAP_BASE_DN", ""),
        "user_template": os.environ.get("LDAP_USER_TEMPLATE", ""),
        "role_map": [dict(r) for r in rows],
    }


@router.post("/directory/check", response_model=DirectoryCheck)
async def directory_check(_user: asyncpg.Record = Depends(require("settings.write"))):
    ok, ms, message = await run_in_threadpool(ldap_auth.check_connection)
    return {"ok": ok, "ms": ms, "message": message}


@router.get("/users", response_model=list[UserItem])
async def list_users(
    conn: asyncpg.Connection = Depends(get_conn),
    _user: asyncpg.Record = Depends(require("settings.read")),
):
    rows = await conn.fetch(
        """
        SELECT u.login, u.full_name, u.auth_source, u.password_hash, u.is_active,
               ARRAY(SELECT r.role_code FROM ref.user_role r
                      WHERE r.login = u.login ORDER BY r.role_code) AS roles
          FROM ref.app_user u
         ORDER BY u.login
        """
    )
    return [_user_item(r) for r in rows]


@router.patch("/users/{login}", response_model=UserItem)
async def update_user(
    login: str,
    body: UserUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    user: asyncpg.Record = Depends(require("settings.write")),
):
    row = await _patch_user_core(login, body, request, conn, user["login"])
    return _user_item(row)


def _selfcheck():
    import asyncio

    # DEMO_ACCOUNTS — против argon2-хешей db/seed/rbac.sql. Разойдётся сид с
    # константой — эта проверка первой покраснеет, а не подсказка на экране входа.
    _SEED_HASHES = {
        "disp2": "$argon2id$v=19$m=65536,t=3,p=4$HxqQqqxM0wRtfSbUZGG+Lw$Ga/XIR9roTT9R7A+mhyXx/WvbKGBWNEVG8Itb451iqE",
        "ods1": "$argon2id$v=19$m=65536,t=3,p=4$OP7Y01uuWH56hJrmN4ssLA$DSrZdqPpPn+s6XL+B00xTP+bu502QCJUHQOYCxw//S8",
        "tech1": "$argon2id$v=19$m=65536,t=3,p=4$VH5tU5alwapN3s4VKo3NfQ$8fMA7COqLtjQ5IlzFLU7Y+MIvmCnny+ENVITMG7yBH4",
        "admin1": "$argon2id$v=19$m=65536,t=3,p=4$hi21eVs/+Z4TC+MviMr/qQ$fiWyF7z+LFJYhUBo/YKjDMBOn8c7hbnKcDFs1/4/ud4",
    }
    for acc in DEMO_ACCOUNTS:
        assert verify_password(_SEED_HASHES[acc["login"]], acc["password"]), (
            f"подсказка {acc['login']} разошлась с хешем в db/seed/rbac.sql"
        )

    class _FakeConn:
        """login -> {full_name, auth_source, password_hash, is_active, roles}.

        _sync_ldap_user пишет тремя запросами (app_user, user_role, user_scope) —
        мок отражает их в тот же словарь, а не просто копит вызовы: без этого
        повторный _load_user после синхронизации получил бы None, как настоящая
        база, где строку пишет предыдущий execute.
        """

        class _NoopTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        def __init__(self, users, role_map):
            self._users = {login: dict(row) for login, row in users.items()}
            self._role_map = role_map
            self.inserted_app_user = False

        def transaction(self):
            return self._NoopTransaction()

        async def fetchrow(self, _sql, login):
            row = self._users.get(login)
            if row is None:
                return None
            return {"user_id": row.get("user_id", 1), "login": login, **row}

        async def fetch(self, _sql):
            return [{"group_cn": g, "role_code": r, "object_id": o} for g, r, o in self._role_map]

        async def execute(self, sql, *args):
            if "INSERT INTO ref.app_user" in sql:
                login = args[0]
                self._users.setdefault(login, {"roles": []})
                self._users[login].update(
                    {"full_name": login, "auth_source": "ldap", "password_hash": None, "is_active": True}
                )
                self.inserted_app_user = True
            elif "DELETE FROM ref.user_role" in sql:
                login = args[0]
                if login in self._users:
                    self._users[login]["roles"] = []
            elif "UPDATE ref.app_user SET is_active" in sql:
                is_active, login = args
                self._users[login]["is_active"] = is_active

        async def executemany(self, sql, rows):
            if "ref.user_role" in sql:
                for login, role in rows:
                    self._users.setdefault(login, {"roles": []})
                    self._users[login].setdefault("roles", []).append(role)

    class _FakeRequest:
        def __init__(self):
            self.state = type("_State", (), {})()

    def local_user(login, roles, active=True, password="Пароль12345"):
        from app.auth.password import hash_password

        return {
            "user_id": 1, "login": login, "full_name": login, "auth_source": "local",
            "password_hash": hash_password(password), "is_active": active, "roles": roles,
        }

    async def run():
        # Неизвестный логин без LDAP_URI — 401, а не 500.
        os.environ.pop("LDAP_URI", None)
        conn = _FakeConn(users={}, role_map=[])
        req = _FakeRequest()
        try:
            await _login_core(LoginBody(login="ghost", password="x"), req, conn)
        except HTTPException as e:
            assert e.status_code == 401
            assert req.state.audit_details == {"login_attempt": "ghost"}
        else:
            raise AssertionError("неизвестный логин без LDAP_URI должен дать 401")

        # Локальный: неверный пароль — 401 (задание A, п. 17).
        conn = _FakeConn(users={"admin1": local_user("admin1", ["admin"])}, role_map=[])
        try:
            await _login_core(LoginBody(login="admin1", password="неверный"), _FakeRequest(), conn)
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("неверный пароль должен дать 401")

        # Локальный: заблокированная запись — 401, до сверки пароля.
        conn = _FakeConn(users={"admin1": local_user("admin1", ["admin"], active=False)}, role_map=[])
        try:
            await _login_core(LoginBody(login="admin1", password="Пароль12345"), _FakeRequest(), conn)
        except HTTPException as e:
            assert e.status_code == 401
        else:
            raise AssertionError("заблокированная запись должна дать 401")

        # Локальный: верный пароль — вход, ttl местный (8 ч).
        conn = _FakeConn(users={"admin1": local_user("admin1", ["admin"])}, role_map=[])
        user, ttl = await _login_core(LoginBody(login="admin1", password="Пароль12345"), _FakeRequest(), conn)
        assert user["login"] == "admin1" and ttl == TTL_LOCAL_S

        # LDAP: каталог настроен, но группа не сопоставлена (человека убрали
        # из групп) — authenticate() возвращает пустой список, не None. 401.
        os.environ["LDAP_URI"] = "ldap://demo.invalid"
        os.environ["LDAP_USER_TEMPLATE"] = "uid={login},ou=people,dc=x"
        os.environ["LDAP_BASE_DN"] = "dc=x"
        orig_authenticate = ldap_auth.authenticate
        try:
            ldap_auth.authenticate = lambda login, password: []  # bind ок, ноль групп
            conn = _FakeConn(users={}, role_map=[("role-admin", "admin", None)])
            try:
                await _login_core(LoginBody(login="ldap_admin1", password="x"), _FakeRequest(), conn)
            except HTTPException as e:
                assert e.status_code == 401
            else:
                raise AssertionError("ноль групп должно дать 401")

            # LDAP: неверный пароль (в т.ч. пустой, RFC 4513 5.1.2) — authenticate() вернёт None.
            ldap_auth.authenticate = lambda login, password: None
            conn = _FakeConn(users={}, role_map=[])
            try:
                await _login_core(LoginBody(login="ldap_admin1", password=""), _FakeRequest(), conn)
            except HTTPException as e:
                assert e.status_code == 401
            else:
                raise AssertionError("неверные логин/пароль (пустой пароль) должны дать 401")

            # LDAP: bind ок, группа сопоставлена — вход, ttl короче (1 ч), запись заведена.
            ldap_auth.authenticate = lambda login, password: ["role-admin"]
            conn = _FakeConn(users={}, role_map=[("role-admin", "admin", None)])
            user, ttl = await _login_core(LoginBody(login="ldap_admin1", password="x"), _FakeRequest(), conn)
            assert user["auth_source"] == "ldap" and ttl == TTL_LDAP_S
            assert user["password_hash"] is None
            assert conn.inserted_app_user

            # LDAP: каталог не отвечает — 401, а не 500 (задание A, п. 9).
            def _unavailable(login, password=None):
                raise ldap_auth.DirectoryUnavailable("таймаут")

            ldap_auth.authenticate = _unavailable
            conn = _FakeConn(users={}, role_map=[])
            try:
                await _login_core(LoginBody(login="ldap_admin1", password="x"), _FakeRequest(), conn)
            except HTTPException as e:
                assert e.status_code == 401
            else:
                raise AssertionError("недоступный каталог должен дать 401, не 500")
        finally:
            ldap_auth.authenticate = orig_authenticate
            os.environ.pop("LDAP_URI", None)
            os.environ.pop("LDAP_USER_TEMPLATE", None)
            os.environ.pop("LDAP_BASE_DN", None)

        # PATCH /users — блокировка администратором (MOS-226, Ф-66).
        conn = _FakeConn(
            users={
                "admin1": local_user("admin1", ["admin"]),
                "tech2": local_user("tech2", ["technician"]),
            },
            role_map=[],
        )

        # 404 — логина нет.
        try:
            await _patch_user_core("ghost", UserUpdate(is_active=False), _FakeRequest(), conn, "admin1")
        except HTTPException as e:
            assert e.status_code == 404
        else:
            raise AssertionError("несуществующий логин должен дать 404")

        # 400 — администратор блокирует сам себя.
        try:
            await _patch_user_core("admin1", UserUpdate(is_active=False), _FakeRequest(), conn, "admin1")
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError("блокировка себя должна дать 400")

        # Блокировка чужого — 200, is_active падает, audit_details несёт old/new.
        req = _FakeRequest()
        row = await _patch_user_core("tech2", UserUpdate(is_active=False), req, conn, "admin1")
        assert row["is_active"] is False
        assert req.state.audit_details == {"login": "tech2", "old": True, "new": False}

        # Разблокировка обратно — old теперь False.
        req = _FakeRequest()
        row = await _patch_user_core("tech2", UserUpdate(is_active=True), req, conn, "admin1")
        assert row["is_active"] is True
        assert req.state.audit_details == {"login": "tech2", "old": False, "new": True}

    asyncio.run(run())
    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
