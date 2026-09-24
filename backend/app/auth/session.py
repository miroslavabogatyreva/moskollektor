"""Подпись cookie mk_session — HMAC-SHA256 на стандартной библиотеке. Задача
MOS-39 (Q4.2). Новых зависимостей для куки не заводим — hmac/base64/json/time.

Ключ — переменная окружения AUTH_SECRET (docs/HLD.md разд. 3.5); без неё
подписывать и проверять куку нечем, поэтому модуль падает при импорте понятной
ошибкой, а не KeyError где-то в середине запроса на входе.

Срок сессии разный (решение Славы 24.09.2026): LDAP-вход короче локального —
администратор каталога может заблокировать учётку в любой момент, и час —
тот срок, за который блокировка успевает подействовать без ожидания истечения
куки; локальная запись — запасной путь входа, поэтому 8 часов.
"""
import base64
import hashlib
import hmac
import json
import os
import time

try:
    AUTH_SECRET = os.environ["AUTH_SECRET"]
except KeyError as e:
    raise RuntimeError(
        "AUTH_SECRET не задан — без него нечем подписывать cookie mk_session"
        " (docs/HLD.md разд. 3.5)"
    ) from e

TTL_LOCAL_S = 8 * 3600
TTL_LDAP_S = 1 * 3600


def sign_session(login: str, ttl_seconds: int) -> str:
    payload = json.dumps(
        {"login": login, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")
    ).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = hmac.new(AUTH_SECRET.encode(), payload_b64, hashlib.sha256).hexdigest()
    return f"{payload_b64.decode()}.{sig}"


def verify_session(token: str) -> str | None:
    """Логин из подписанной и не истёкшей куки, иначе None (маршрут отвечает 401)."""
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(AUTH_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    login = payload.get("login")
    return login if isinstance(login, str) else None


def _selfcheck():
    token = sign_session("admin1", TTL_LOCAL_S)
    assert verify_session(token) == "admin1"

    # Подделанная подпись куки — 401 (задание A, п. 8: попытка сфабриковать вход).
    login_part, _sig = token.split(".", 1)
    forged = f"{login_part}.{'0' * 64}"
    assert verify_session(forged) is None

    # Истёкшая сессия — отрицательный ttl эмулирует уже прошедший срок.
    assert verify_session(sign_session("admin1", -1)) is None

    # Мусор вместо куки не падает исключением, а отвечает «нет сессии».
    assert verify_session("совсем не похоже на куку") is None
    assert verify_session("a.b.c") is None

    # LDAP-сессия короче локальной — тот же формат, другой срок в подписанной части.
    ldap_token = sign_session("ldap_admin1", TTL_LDAP_S)
    assert verify_session(ldap_token) == "ldap_admin1"

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
