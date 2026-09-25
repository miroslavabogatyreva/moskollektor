#!/usr/bin/env python3
"""Вход по паролю и каталогу против стенда: строки Ф-66, НФ-43, НФ-76, НФ-77. MOS-39 (Q4.2).

**Зачем.** До MOS-39 личность бралась из заголовка X-User-Login без пароля, и все
проверки доступа (check_scope в delivery/check-all.sh) шли через него. Здесь то же
самое проверяется так, как войдёт эксперт: паролем, с кукой mk_session, без заголовка.

Что проверяем, по шагам:
1. GET /api/auth/info отдаёт демо-учётки, и КАЖДЫЙ пароль из подсказки реально входит.
   Подсказку пишет константа в коде, хеш — db/seed/rbac.sql; разойдутся они молча,
   и эксперт увидит на экране входа пароль, который не подходит.
2. Кука mk_session: HttpOnly, Secure, SameSite=Lax, Path=/; срок по Max-Age — 8 ч
   у локальной учётки, 1 ч у учётки каталога (решение оркестратора 24.09.2026:
   блокировка в каталоге не гасит открытую сессию, поэтому ей короткий срок).
3. Отказы: неверный пароль и несуществующий логин — оба 401 с ОДИНАКОВЫМ текстом
   (по разнице текстов перебором узнают, какие логины есть); без куки — 401;
   кука с испорченной подписью — 401.
4. Ф-66: /api/risks с кукой tech1 короче, чем с кукой ods1 (подрезка по области
   видимости работает и после смены способа входа). «Служба каталогов» —
   admin1 200, tech1 403.
5. Выход: 204 и Set-Cookie, стирающий mk_session.
6. НФ-77 (DATABASE_URL): строка audit.user_action после запроса с кукой несёт
   user_id вошедшего, и строка самого успешного POST /api/auth/login — тоже.
7. НФ-76 (LDAP_LOGIN, LDAP_PASSWORD, STAND_SSH): учётка каталога входит → 200,
   password_hash у неё в базе пустой; block-user.sh → вход 401; block-user.sh
   --undo → снова 200. Возврат стоит в finally: проверка с записью обязана
   вернуть прочитанное, даже если упала посередине.

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k python3 code/check_auth.py
    python3 code/check_auth.py --selfcheck
"""

import http.cookies
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

КУКА = "mk_session"
СРОК_ЛОКАЛЬНОЙ = 8 * 3600
СРОК_КАТАЛОГА = 3600
# Путь на стенде, куда выкладка кладёт deploy/ (docs/server.md).
БЛОКИРОВКА = "/srv/moskollektor/deploy/ldap/block-user.sh"


class _БезПереадресаций(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def разобрать_куку(set_cookie):
    """Set-Cookie → (значение, {атрибут в нижнем регистре: значение}). None, если это не mk_session."""
    c = http.cookies.SimpleCookie()
    c.load(set_cookie)
    if КУКА not in c:
        return None
    m = c[КУКА]
    атрибуты = {k: m[k] for k in m.keys() if m[k] != ""}
    # SimpleCookie отдаёт флаги HttpOnly/Secure как True, а samesite — строкой.
    return m.value, атрибуты


def нарушения_куки(атрибуты, срок):
    """Список нарушений договора куки; пустой — всё верно."""
    плохо = []
    for флаг in ("httponly", "secure"):
        if not атрибуты.get(флаг):
            плохо.append(f"нет {флаг}")
    if str(атрибуты.get("samesite", "")).lower() != "lax":
        плохо.append(f"samesite={атрибуты.get('samesite')!r}, ждали Lax")
    if атрибуты.get("path") != "/":
        плохо.append(f"path={атрибуты.get('path')!r}, ждали /")
    max_age = атрибуты.get("max-age")
    if str(max_age) != str(срок):
        плохо.append(f"max-age={max_age!r}, ждали {срок}")
    return плохо


def испортить_подпись(значение):
    """Меняет последний символ: подпись в конце, значение остаётся той же длины и алфавита."""
    return значение[:-1] + ("A" if значение[-1] != "A" else "B")


class Стенд:
    def __init__(self, base, verify):
        self.base = base.rstrip("/")
        ctx = None if verify else ssl._create_unverified_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx), _БезПереадресаций()
        )

    def запрос(self, метод, путь, тело=None, кука=None):
        """→ (код, тело как объект или None, список Set-Cookie). Заголовка X-User-Login нет нигде."""
        данные = json.dumps(тело).encode() if тело is not None else None
        req = urllib.request.Request(self.base + путь, data=данные, method=метод)
        if данные is not None:
            req.add_header("Content-Type", "application/json")
        if кука:
            req.add_header("Cookie", f"{КУКА}={кука}")
        try:
            r = self.opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        сырое = r.read()
        try:
            объект = json.loads(сырое) if сырое else None
        except ValueError:
            объект = None
        return r.status if hasattr(r, "status") else r.code, объект, r.headers.get_all("Set-Cookie") or []

    def войти(self, логин, пароль):
        """→ (код, тело, (значение куки, атрибуты) или None)."""
        код, тело, куки = self.запрос("POST", "/api/auth/login", {"login": логин, "password": пароль})
        кука = next((к for к in map(разобрать_куку, куки) if к), None)
        return код, тело, кука


def длина(тело):
    if isinstance(тело, list):
        return len(тело)
    return тело["total"] if "total" in тело else len(тело["items"])


def проверить_стенд(с):
    итог = []

    код, info, _ = с.запрос("GET", "/api/auth/info")
    assert код == 200, f"/api/auth/info: {код}, ждали 200 без входа"
    демо = info.get("demo_accounts") or []
    assert демо, "/api/auth/info: demo_accounts пуст — на стенде AUTH_DEMO_HINTS=1, подсказка обязана быть"

    куки = {}
    for у in демо:
        код, тело, кука = с.войти(у["login"], у["password"])
        assert код == 200, f"подсказка разошлась с сидом: {у['login']} с паролем из /api/auth/info → {код}"
        assert кука, f"{у['login']}: 200 без Set-Cookie {КУКА}"
        assert тело.get("login") == у["login"] and тело.get("roles"), f"{у['login']}: тело входа {тело}"
        assert тело.get("auth_source") == "local", f"{у['login']}: auth_source {тело.get('auth_source')}, демо-учётки локальные"
        плохо = нарушения_куки(кука[1], СРОК_ЛОКАЛЬНОЙ)
        assert not плохо, f"{у['login']}: кука — {', '.join(плохо)}"
        код, me, _ = с.запрос("GET", "/api/auth/me", кука=кука[0])
        assert код == 200 and me.get("login") == у["login"], f"{у['login']}: /api/auth/me → {код} {me}"
        куки[у["login"]] = кука[0]
    итог.append(f"вход: {len(демо)} демо-учёток из подсказки, кука 8 ч с HttpOnly/Secure/Lax")

    первый = демо[0]
    код_пароль, тело_пароль, кука = с.войти(первый["login"], первый["password"] + "x")
    assert код_пароль == 401 and not кука, f"неверный пароль {первый['login']}: {код_пароль}, ждали 401 без куки"
    код_логин, тело_логин, _ = с.войти("nobody_mos39", первый["password"])
    assert код_логин == 401, f"неизвестный логин: {код_логин}, ждали 401"
    assert тело_пароль == тело_логин, (
        f"тексты отказа различаются: {тело_пароль} против {тело_логин} — по ним узнают, какой логин есть"
    )
    код, _, _ = с.запрос("GET", "/api/risks")
    assert код == 401, f"/api/risks без куки и заголовка: {код}, ждали 401"
    код, _, _ = с.запрос("GET", "/api/risks", кука=испортить_подпись(куки[первый["login"]]))
    assert код == 401, f"/api/risks с испорченной подписью куки: {код}, ждали 401"
    итог.append("отказы: пароль, логин (один текст), без куки, подпись — 401")

    for нужен in ("tech1", "ods1", "admin1"):
        assert нужен in куки, f"в подсказке нет {нужен}"
    код_t, риски_t, _ = с.запрос("GET", "/api/risks", кука=куки["tech1"])
    код_o, риски_o, _ = с.запрос("GET", "/api/risks", кука=куки["ods1"])
    assert код_t == 200 and код_o == 200, f"/api/risks: tech1 {код_t}, ods1 {код_o}"
    n_t, n_o = длина(риски_t), длина(риски_o)
    assert 0 < n_t < n_o, f"Ф-66: /api/risks tech1 {n_t}, ods1 {n_o} — ждали 0 < tech1 < ods1"
    код_a, _, _ = с.запрос("GET", "/api/auth/directory", кука=куки["admin1"])
    код_t, _, _ = с.запрос("GET", "/api/auth/directory", кука=куки["tech1"])
    assert (код_a, код_t) == (200, 403), f"служба каталогов: admin1 {код_a}, tech1 {код_t} — ждали 200 и 403"
    итог.append(f"Ф-66: /api/risks tech1 {n_t} < ods1 {n_o}; каталог admin1 200, tech1 403")

    # Выход — на отдельной сессии, чтобы не тратить куки, нужные ниже.
    _, _, кука = с.войти(первый["login"], первый["password"])
    код, _, set_cookie = с.запрос("POST", "/api/auth/logout", кука=кука[0])
    стёрта = [разобрать_куку(s) for s in set_cookie]
    стёрта = next((к for к in стёрта if к), None)
    assert код == 204, f"выход: {код}, ждали 204"
    assert стёрта and (стёрта[0] == "" or str(стёрта[1].get("max-age")) == "0"), (
        f"выход не стёр {КУКА}: Set-Cookie {set_cookie}"
    )
    итог.append("выход: 204, кука стёрта")

    if os.environ.get("DATABASE_URL"):
        итог.append(проверить_журнал(с, первый))

    if os.environ.get("LDAP_LOGIN"):
        итог.append(проверить_каталог(с))
    else:
        итог.append("ВНИМАНИЕ: НФ-76 не проверена — задайте LDAP_LOGIN, LDAP_PASSWORD и STAND_SSH")
    return итог


def _база(sql, *args):
    import asyncio

    import asyncpg

    async def m():
        c = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await c.fetch(sql, *args)
        finally:
            await c.close()

    return asyncio.run(m())


def проверить_журнал(с, учётка):
    """НФ-77: строки журнала после входа и после запроса с кукой несут user_id вошедшего."""
    до = _база("SELECT coalesce(max(action_id), 0) AS n FROM audit.user_action")[0]["n"]
    _, _, кука = с.войти(учётка["login"], учётка["password"])
    с.запрос("GET", "/api/auth/me", кука=кука[0])
    строки = _база(
        """SELECT a.path, a.status_code, u.login
             FROM audit.user_action a LEFT JOIN ref.app_user u USING (user_id)
            WHERE a.action_id > $1 AND a.path IN ('/api/auth/login', '/api/auth/me')
            ORDER BY a.action_id""",
        до,
    )
    по_пути = {r["path"]: r for r in строки}
    for путь in ("/api/auth/login", "/api/auth/me"):
        r = по_пути.get(путь)
        assert r, f"НФ-77: после {путь} строки в audit.user_action нет"
        assert r["login"] == учётка["login"], (
            f"НФ-77: строка {путь} ({r['status_code']}) записана за {r['login']!r}, вошёл {учётка['login']}"
        )
    return f"НФ-77: вход и /api/auth/me записаны за {учётка['login']}"


def _блокировка(логин, *флаги):
    ssh = os.environ["STAND_SSH"]
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", ssh, "sh", БЛОКИРОВКА, *флаги, логин],
        check=True, capture_output=True, timeout=60,
    )


def проверить_каталог(с):
    """НФ-76: вход через каталог, блокировка в каталоге, возврат учётки."""
    логин, пароль = os.environ["LDAP_LOGIN"], os.environ["LDAP_PASSWORD"]
    код, тело, кука = с.войти(логин, пароль)
    assert код == 200, f"НФ-76: {логин} через каталог → {код}, ждали 200"
    assert тело.get("auth_source") == "ldap", f"НФ-76: {логин} вошёл как {тело.get('auth_source')}, ждали ldap"
    плохо = нарушения_куки(кука[1], СРОК_КАТАЛОГА)
    assert not плохо, f"{логин}: кука — {', '.join(плохо)}"
    if os.environ.get("DATABASE_URL"):
        хеш = _база("SELECT password_hash FROM ref.app_user WHERE login = $1", логин)
        assert хеш and хеш[0]["password_hash"] is None, f"НФ-76: у {логин} в базе лежит пароль"
    _блокировка(логин)
    try:
        код_блок, _, кука = с.войти(логин, пароль)
        assert код_блок == 401 and not кука, f"НФ-76: после block-user.sh {логин} → {код_блок}, ждали 401"
    finally:
        _блокировка(логин, "--undo")
    код_снова, _, _ = с.войти(логин, пароль)
    assert код_снова == 200, f"НФ-76: после --undo {логин} → {код_снова}, учётка не вернулась"
    return f"НФ-76: {логин} через каталог 200, кука 1 ч, заблокирован 401, возвращён 200"


def _selfcheck():
    значение, а = разобрать_куку(
        f"{КУКА}=abc.def; HttpOnly; Max-Age=28800; Path=/; SameSite=Lax; Secure"
    )
    assert значение == "abc.def" and нарушения_куки(а, СРОК_ЛОКАЛЬНОЙ) == []
    # Каждый атрибут договора ловится по отдельности.
    for без, ждём in (("HttpOnly; ", "httponly"), ("Secure", "secure"), ("SameSite=Lax; ", "samesite"),
                      ("Path=/; ", "path")):
        _, а = разобрать_куку(f"{КУКА}=abc; HttpOnly; Max-Age=28800; Path=/; SameSite=Lax; Secure".replace(без, ""))
        assert any(ждём in п for п in нарушения_куки(а, СРОК_ЛОКАЛЬНОЙ)), без
    # Срок сверяется точно: кука каталога на 8 ч — нарушение.
    _, а = разобрать_куку(f"{КУКА}=abc; HttpOnly; Max-Age=28800; Path=/; SameSite=Lax; Secure")
    assert нарушения_куки(а, СРОК_КАТАЛОГА) == ["max-age='28800', ждали 3600"]
    assert разобрать_куку("other=1; Path=/") is None
    assert испортить_подпись("xyzA") == "xyzB" and испортить_подпись("xyz9") == "xyzA"
    assert длина([1, 2]) == 2 and длина({"total": 7, "items": []}) == 7
    print("selfcheck ok")


def main():
    if "--selfcheck" in sys.argv:
        _selfcheck()
        return 0
    base = os.environ.get("BASE_URL")
    if not base:
        print("задайте BASE_URL", file=sys.stderr)
        return 1
    с = Стенд(base, verify="-k" not in os.environ.get("CURL_OPTS", "").split())
    try:
        for строка in проверить_стенд(с):
            print(строка)
    except AssertionError as e:
        print(f"УПАЛА: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
