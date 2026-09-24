#!/usr/bin/env python3
"""Администратор блокирует пользователя из интерфейса: строка приёмки Ф-66. MOS-226 (Q4.19).

**Зачем.** Ф-66 требует, чтобы администратор мог закрыть вход учётке. Флаг
ref.app_user.is_active есть с 008_rbac.sql, вход и каждый запрос его читают
(backend/app/api/auth.py, backend/app/auth/deps.py), а менять его до MOS-226
можно было только руками в базе. Здесь то, что сделает администратор на экране
«Пользователи», идёт теми же двумя методами API, что и у экрана:
GET /api/auth/users и PATCH /api/auth/users/{login}.

Шаги. Подопытный — tech2: демо на странице входа его не показывает, а на tech1,
ods1, dispatcher1, admin1 держатся другие строки check-all.
1. admin1 читает список: tech2 в нём есть, список отсортирован по login.
   dispatcher1 получает 403 — список учёток видит только администратор.
2. tech2 входит, его кука запоминается (дальше называю её старой).
3. admin1 блокирует tech2 → 200 и is_active=false в ответе. После этого вход
   tech2 даёт 401, старая кука на /api/auth/me тоже 401: блокировка закрывает
   и новые входы, и уже открытую сессию.
4. admin1 разблокирует → вход tech2 снова 200.
5. PATCH несуществующего логина → 404.
6. С DATABASE_URL: строка audit.user_action блокировки несёт
   details = {login: tech2, old: true, new: false}.
В finally is_active tech2 возвращается к значению, прочитанному в шаге 1:
проверка с записью обязана вернуть прочитанное, даже если упала посередине.

Блокировку себя (400) здесь не проверяем нарочно: при сломанной защите admin1
заблокирует сам себя, и вернуть его сможет только правка базы руками. Эту ветку
проверяет самопроверка бэкенда.

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k TECH2_PASSWORD=… python3 code/check_block_user.py
"""

import json
import os
import sys

from check_auth import Стенд, _база

ПОДОПЫТНЫЙ = "tech2"


def _статус(тело):
    return тело.get("is_active") if isinstance(тело, dict) else None


def проверить(с, пароль_подопытного):
    итог = []
    _, info, _ = с.запрос("GET", "/api/auth/info")
    пароли = {у["login"]: у["password"] for у in (info or {}).get("demo_accounts") or []}
    assert {"admin1", "dispatcher1"} <= пароли.keys(), f"в подсказке /api/auth/info нет admin1 или dispatcher1: {sorted(пароли)}"
    кука = {}
    for логин in ("admin1", "dispatcher1"):
        код, _, к = с.войти(логин, пароли[логин])
        assert код == 200 and к, f"{логин} не вошёл: {код}"
        кука[логин] = к[0]

    код, список, _ = с.запрос("GET", "/api/auth/users", кука=кука["admin1"])
    assert код == 200 and isinstance(список, list), f"GET /api/auth/users admin1: {код}"
    логины = [u["login"] for u in список]
    assert логины == sorted(логины), f"список не отсортирован по login: {логины}"
    по_логину = {u["login"]: u for u in список}
    assert ПОДОПЫТНЫЙ in по_логину, f"{ПОДОПЫТНЫЙ} нет в GET /api/auth/users: {логины}"
    исходное = по_логину[ПОДОПЫТНЫЙ]["is_active"]
    assert isinstance(исходное, bool), f"is_active {ПОДОПЫТНЫЙ}: {исходное!r}, ждали bool"
    код, _, _ = с.запрос("GET", "/api/auth/users", кука=кука["dispatcher1"])
    assert код == 403, f"GET /api/auth/users dispatcher1: {код}, ждали 403"
    итог.append(f"список: {len(список)} учёток по login, {ПОДОПЫТНЫЙ} is_active={исходное}; dispatcher1 403")

    путь = f"/api/auth/users/{ПОДОПЫТНЫЙ}"

    def поставить(значение):
        код, тело, _ = с.запрос("PATCH", путь, {"is_active": значение}, кука=кука["admin1"])
        assert код == 200 and _статус(тело) is значение, f"PATCH {путь} is_active={значение}: {код} {тело}"
        return тело

    до_блокировки = None
    if os.environ.get("DATABASE_URL"):
        до_блокировки = _база("SELECT coalesce(max(action_id), 0) AS n FROM audit.user_action")[0]["n"]
    try:
        if not исходное:
            поставить(True)
        код, _, старая = с.войти(ПОДОПЫТНЫЙ, пароль_подопытного)
        assert код == 200 and старая, f"{ПОДОПЫТНЫЙ} активен, а вход {код} — неверный TECH2_PASSWORD?"
        код, me, _ = с.запрос("GET", "/api/auth/me", кука=старая[0])
        assert код == 200, f"/api/auth/me {ПОДОПЫТНЫЙ} до блокировки: {код} {me}"

        тело = поставить(False)
        assert тело.get("login") == ПОДОПЫТНЫЙ and "roles" in тело, f"ответ PATCH не элемент списка: {тело}"
        код_вход, _, к = с.войти(ПОДОПЫТНЫЙ, пароль_подопытного)
        assert код_вход == 401 and not к, f"Ф-66: заблокированный {ПОДОПЫТНЫЙ} входит: {код_вход}, ждали 401"
        код_кука, _, _ = с.запрос("GET", "/api/auth/me", кука=старая[0])
        assert код_кука == 401, f"Ф-66: старая кука {ПОДОПЫТНЫЙ} после блокировки: {код_кука}, ждали 401"
        итог.append(f"блокировка: PATCH 200, вход {код_вход}, старая кука {код_кука}")

        поставить(True)
        код, _, к = с.войти(ПОДОПЫТНЫЙ, пароль_подопытного)
        assert код == 200 and к, f"после разблокировки {ПОДОПЫТНЫЙ} не входит: {код}"
        итог.append(f"разблокировка: PATCH 200, вход {код}")
    finally:
        # Возврат к прочитанному — отдельным запросом, без assert: уборка не роняет
        # проверку сама, но и не молчит, если не вышла.
        код, тело, _ = с.запрос("PATCH", путь, {"is_active": исходное}, кука=кука["admin1"])
        if код != 200 or _статус(тело) is not исходное:
            print(f"ВНИМАНИЕ: {ПОДОПЫТНЫЙ} не вернулся к is_active={исходное}: {код} {тело}")

    код, _, _ = с.запрос("PATCH", "/api/auth/users/nobody_mos226", {"is_active": True}, кука=кука["admin1"])
    assert код == 404, f"PATCH несуществующего логина: {код}, ждали 404"

    код, список, _ = с.запрос("GET", "/api/auth/users", кука=кука["admin1"])
    в_конце = {u["login"]: u["is_active"] for u in список}[ПОДОПЫТНЫЙ]
    assert в_конце is исходное, f"{ПОДОПЫТНЫЙ}: в начале is_active={исходное}, в конце {в_конце}"
    итог.append(f"404 на чужой логин; в конце is_active={в_конце}, как в начале")

    if до_блокировки is not None:
        строки = _база(
            """SELECT a.details FROM audit.user_action a
                WHERE a.action_id > $1 AND a.method = 'PATCH' AND a.path = $2 AND a.status_code = 200
                ORDER BY a.action_id""",
            до_блокировки, путь,
        )
        детали = [json.loads(r["details"]) if isinstance(r["details"], str) else r["details"] for r in строки]
        блок = [d for d in детали if d and str(d.get("new")).lower() == "false"]
        assert блок, f"НФ-77: в audit.user_action нет строки блокировки {путь} с new=false: {детали}"
        d = блок[0]
        assert d.get("login") == ПОДОПЫТНЫЙ and str(d.get("old")).lower() == "true", f"НФ-77: details блокировки {d}"
        итог.append(f"журнал: {len(строки)} строк PATCH, у блокировки details={d}")
    else:
        итог.append("ВНИМАНИЕ: журнал блокировки не проверен — задайте DATABASE_URL")
    return итог


def main():
    base, пароль = os.environ.get("BASE_URL"), os.environ.get("TECH2_PASSWORD")
    if not base or not пароль:
        print("задайте BASE_URL и TECH2_PASSWORD", file=sys.stderr)
        return 1
    с = Стенд(base, verify="-k" not in os.environ.get("CURL_OPTS", "").split())
    try:
        for строка in проверить(с, пароль):
            print(строка)
    except AssertionError as e:
        print(f"УПАЛА: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
