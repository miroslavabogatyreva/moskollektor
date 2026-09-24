"""Хеширование и проверка паролей локальных учётных записей. Задача MOS-39
(Q4.2). argon2-cffi, лицензия MIT, в поставке разрешена (docs/HLD.md разд. 7.3).

Один общий `PasswordHasher` с настройками библиотеки по умолчанию — правило
паролей (минимум 8 символов, срок не больше 90 суток, docs/HLD.md разд. 3.5)
проверяется до вызова hash_password, не здесь.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _selfcheck():
    h = hash_password("Пароль12345")
    assert verify_password(h, "Пароль12345") is True

    # Неверный пароль — 401 в маршруте входа (задание A, п. 17).
    assert verify_password(h, "неверный") is False
    assert verify_password(h, "") is False

    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
