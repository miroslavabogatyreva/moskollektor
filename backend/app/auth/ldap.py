"""LDAP simple bind и сопоставление групп каталога с ролями. Задача MOS-39
(Q4.2). python-ldap, лицензия в стиле Python (классификатор PyPI — PSF);
не `ldap3` — она под LGPLv3, запрещена docs/HLD.md разд. 7.3.

Поиск групп — фильтр `(&(objectClass=groupOfNames)(member=<DN>))` под
LDAP_BASE_DN: работает и без оверлея memberOf, и на AD, второй способ поиска
под конкретный каталог не нужен.

Сетевые вызовы (`bind`, `groups`, `check_connection`) против настоящего LDAP
доказывает `delivery/check-all.sh` на стенде (НФ-76, зона проверяющей 92) —
самопроверка этого модуля тестирует только то, что можно проверить без сети:
шаблон DN, разбор ответа `search_s` и правило «группа → роль/область».
"""
import os
import time

import ldap

_TIMEOUT_S = 3


class DirectoryUnavailable(Exception):
    """Каталог не ответил за _TIMEOUT_S секунд — не то же самое, что неверный пароль."""


def user_dn(login: str) -> str:
    return os.environ["LDAP_USER_TEMPLATE"].format(login=login)


def _connect():
    conn = ldap.initialize(os.environ["LDAP_URI"])
    conn.set_option(ldap.OPT_NETWORK_TIMEOUT, _TIMEOUT_S)
    conn.set_option(ldap.OPT_TIMEOUT, _TIMEOUT_S)
    return conn


def _is_bad_credentials(exc: ldap.LDAPError) -> bool:
    """True — неверный логин или пароль (401). Иначе каталог недоступен."""
    return isinstance(exc, ldap.INVALID_CREDENTIALS)


def bind(login: str, password: str) -> bool:
    """True — bind прошёл. False — неверные логин/пароль.

    DirectoryUnavailable — каталог не ответил: тогда ldap-пользователь не
    входит, а local входит своим путём (задание A, п. 9/17).
    """
    conn = _connect()
    try:
        conn.simple_bind_s(user_dn(login), password)
        return True
    except ldap.LDAPError as e:
        if _is_bad_credentials(e):
            return False
        raise DirectoryUnavailable(str(e)) from e
    finally:
        conn.unbind_s()


def _extract_cns(search_result: list[tuple]) -> list[str]:
    """cn каждой найденной группы. python-ldap отдаёт атрибуты байтами,
    а рефералы — строкой (None, {}), которую search_s кладёт в тот же список."""
    cns = []
    for dn, attrs in search_result:
        if dn is None:
            continue
        for raw in attrs.get("cn", []):
            cns.append(raw.decode() if isinstance(raw, bytes) else raw)
    return cns


def groups(login: str) -> list[str]:
    """CN групп groupOfNames, где login числится member."""
    conn = _connect()
    try:
        conn.simple_bind_s()  # анонимный поиск, как в check_connection
        result = conn.search_s(
            os.environ["LDAP_BASE_DN"],
            ldap.SCOPE_SUBTREE,
            f"(&(objectClass=groupOfNames)(member={user_dn(login)}))",
            ["cn"],
        )
    except ldap.LDAPError as e:
        raise DirectoryUnavailable(str(e)) from e
    finally:
        conn.unbind_s()
    return _extract_cns(result)


def map_groups_to_roles(
    group_cns: list[str], role_map: list[tuple[str, str | None, int | None]]
) -> tuple[list[str], list[int]]:
    """role_map — строки ref.ldap_role_map (group_cn, role_code, object_id):
    каждая даёт ЛИБО роль, ЛИБО узел области видимости.

    Ноль ролей после сопоставления групп — блокировка «убрать из групп
    каталога» тоже работает: маршрут входа отвечает 401 (задание A, п. 4/17).
    """
    roles: set[str] = set()
    scope: set[int] = set()
    group_set = set(group_cns)
    for group_cn, role_code, object_id in role_map:
        if group_cn not in group_set:
            continue
        if role_code is not None:
            roles.add(role_code)
        if object_id is not None:
            scope.add(object_id)
    return sorted(roles), sorted(scope)


def check_connection() -> tuple[bool, int, str]:
    """POST /api/auth/directory/check — анонимный bind, без 500 при недоступности."""
    uri = os.environ.get("LDAP_URI", "")
    if not uri:
        return False, 0, "каталог не настроен"
    started = time.monotonic()
    try:
        conn = _connect()
        conn.simple_bind_s()
        conn.unbind_s()
    except ldap.LDAPError as e:
        return False, int((time.monotonic() - started) * 1000), f"каталог не отвечает: {e}"
    return True, int((time.monotonic() - started) * 1000), "соединение установлено"


def _selfcheck():
    os.environ.setdefault("LDAP_USER_TEMPLATE", "uid={login},ou=people,dc=moskollektor,dc=local")
    assert user_dn("tech1") == "uid=tech1,ou=people,dc=moskollektor,dc=local"

    # Классификация ошибок bind: неверные учётные данные — 401, остальное — каталог недоступен.
    assert _is_bad_credentials(ldap.INVALID_CREDENTIALS()) is True
    assert _is_bad_credentials(ldap.SERVER_DOWN()) is False
    assert _is_bad_credentials(ldap.TIMEOUT()) is False

    # cn приходит байтами от python-ldap; None-DN — реферал, пропускаем.
    raw = [
        (b"cn=role-admin,ou=groups,dc=x", {"cn": [b"role-admin"]}),
        (None, {}),
        (b"cn=scope-tech-6,ou=groups,dc=x", {"cn": [b"scope-tech-6"]}),
    ]
    assert _extract_cns(raw) == ["role-admin", "scope-tech-6"]

    # Строки ref.ldap_role_map демо-каталога (согласованы с 8a): роль ИЛИ область видимости.
    role_map = [
        ("role-dispatcher", "dispatcher", None),
        ("role-ods", "ods_dispatcher", None),
        ("role-tech", "technician", None),
        ("role-admin", "admin", None),
        ("scope-tech-6", None, 6),
    ]
    assert map_groups_to_roles(["role-admin"], role_map) == (["admin"], [])
    assert map_groups_to_roles(["role-tech", "scope-tech-6"], role_map) == (["technician"], [6])

    # Ноль ролей после сопоставления групп — 401 (человека убрали из групп каталога).
    assert map_groups_to_roles([], role_map) == ([], [])
    assert map_groups_to_roles(["ghost-group"], role_map) == ([], [])

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
