"""Подпись cookie mk_session — HMAC-SHA256 на стандартной библиотеке. Задача
MOS-39 (Q4.2). Новых зависимостей для куки не заводим — hmac/base64/json/time.

Ключ — переменная окружения AUTH_SECRET (docs/HLD.md разд. 3.5). Читаем его
ПРИ ПОДПИСИ И ПРОВЕРКЕ, а не при импорте модуля (находка проверяющей 92,
24.09.2026): импорт здесь падал раньше самого запуска api, и падал не только
в нём — `app.auth.deps` и, транзитивно, `app.api.objects` импортируют этот
модуль, и без AUTH_SECRET валились самопроверки и проверки, которым сама
кука не нужна вовсе (check_card_weight, check_tech_events). «Без ключа api
не стартует» теперь решает `require_secret()`, вызванная в запуске
(`backend/app/api/main.py`), а не побочный эффект импорта.

Пустой или короче 32 символов ключ отвергаем тоже (та же находка 92):
`AUTH_SECRET=` — переменная задана, `os.environ["AUTH_SECRET"]` не бросает
KeyError, и HMAC с пустым или предсказуемо коротким ключом подделывается —
92 подписала куку admin1 именно так.

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

_MIN_SECRET_LEN = 32

TTL_LOCAL_S = 8 * 3600
TTL_LDAP_S = 1 * 3600


def _secret() -> str:
    secret = os.environ.get("AUTH_SECRET", "")
    if len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"AUTH_SECRET не задан или короче {_MIN_SECRET_LEN} символов — им подписывается"
            " cookie mk_session, короткий или пустой ключ подделывается (docs/HLD.md разд. 3.5)"
        )
    return secret


def require_secret() -> None:
    """Вызывает запуск api (backend/app/api/main.py) — без ключа процесс
    не должен подняться вовсе, а не упасть на первом запросе входа."""
    _secret()


def sign_session(login: str, ttl_seconds: int) -> str:
    secret = _secret()
    payload = json.dumps(
        {"login": login, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")
    ).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = hmac.new(secret.encode(), payload_b64, hashlib.sha256).hexdigest()
    return f"{payload_b64.decode()}.{sig}"


def verify_session(token: str) -> str | None:
    """Логин из подписанной и не истёкшей куки, иначе None (маршрут отвечает 401)."""
    secret = _secret()
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    # Сравнение байтами, не str (находка проверяющей 92, 24.09.2026):
    # hmac.compare_digest(str, str) бросает TypeError на не-ASCII символах,
    # а sig — из куки, которую прислал клиент. Starlette декодирует Cookie
    # как latin-1, так что не-ASCII там долетает свободно; TypeError изнутри
    # приложения отвечал бы 500 вместо 401 на GET /api/auth/me и везде,
    # где стоит get_current_user. .encode() у str не бросает исключений
    # ни на каких символах — в отличие от сравнения строк.
    if not hmac.compare_digest(sig.encode(), expected.encode()):
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
    os.environ["AUTH_SECRET"] = "x" * _MIN_SECRET_LEN

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

    # Не-ASCII в подписи — 401, а не TypeError/500 (находка 92, 24.09.2026):
    # hmac.compare_digest(str, str) не принимает не-ASCII, а Starlette пускает
    # такую куку в приложение (декодирует Cookie как latin-1).
    assert verify_session("abc.ÿÿ") is None

    # LDAP-сессия короче локальной — тот же формат, другой срок в подписанной части.
    ldap_token = sign_session("ldap_admin1", TTL_LDAP_S)
    assert verify_session(ldap_token) == "ldap_admin1"

    # Пустой и короткий ключ отвергаем — находка 92: `AUTH_SECRET=` подписывал куку молча.
    for короткий in ("", "x" * (_MIN_SECRET_LEN - 1)):
        os.environ["AUTH_SECRET"] = короткий
        try:
            sign_session("admin1", TTL_LOCAL_S)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"ключ длиной {len(короткий)} не должен подписывать")
    os.environ.pop("AUTH_SECRET", None)
    try:
        verify_session(token)
    except RuntimeError:
        pass
    else:
        raise AssertionError("без AUTH_SECRET verify_session не должна проходить")

    os.environ["AUTH_SECRET"] = "x" * _MIN_SECRET_LEN
    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
