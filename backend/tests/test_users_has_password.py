"""US-24 сц. 3: GET /api/auth/users говорит, хранит ли сервис пароль учётки,
но не отдаёт ни пароля, ни хеша. У учётки каталога хеша нет — пароль проверяет LDAP.

Базы не нужно: подставное соединение отвечает строками ref.app_user.
"""

import asyncio

from app.api import auth


class Соединение:
    async def fetch(self, sql, *args):
        return [
            {"login": "admin1", "full_name": "admin1", "auth_source": "local",
             "password_hash": "$argon2id$v=19$…", "is_active": True, "roles": ["admin"]},
            {"login": "ldap_ods1", "full_name": "ldap_ods1", "auth_source": "ldap",
             "password_hash": None, "is_active": True, "roles": ["ods_dispatcher"]},
        ]


def test_has_password_without_hash():
    ответ = asyncio.run(auth.list_users(conn=Соединение(), _user=None))
    assert {u["login"]: u["has_password"] for u in ответ} == {"admin1": True, "ldap_ods1": False}
    for u in ответ:
        assert "password_hash" not in u
