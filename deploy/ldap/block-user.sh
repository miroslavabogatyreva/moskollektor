#!/bin/sh
# НФ-76: заблокировать учётку в демо-каталоге LDAP, не трогая пароль.
# Способ — ldapmodrdn: DN уводится в сторону («uid=<login>__blocked»),
# LDAP_USER_TEMPLATE перестаёт находить пользователя по прежнему DN,
# bind отвечает «Invalid credentials» (49) — так же, как на неверный пароль,
# без различения причины (ДОГОВОР API). --undo возвращает DN на место.
#
# Запуск на стенде: sh /srv/moskollektor/deploy/ldap/block-user.sh [--undo] <login>
set -eu

BASE_DN="dc=moskollektor,dc=local"
PEOPLE_DN="ou=people,${BASE_DN}"
ADMIN_DN="cn=admin,${BASE_DN}"
ADMIN_PW="LdapAdmin#2026"
SUFFIX="__blocked"
COMPOSE_DIR="$(dirname "$0")/.."

UNDO=0
if [ "${1:-}" = "--undo" ]; then
    UNDO=1
    shift
fi
LOGIN="${1:-}"
if [ -z "$LOGIN" ]; then
    echo "Использование: block-user.sh [--undo] <login>" >&2
    exit 1
fi

modrdn() {
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" --project-directory "${COMPOSE_DIR}" \
        --profile ldap exec -T ldap \
        ldapmodrdn -x -D "$ADMIN_DN" -w "$ADMIN_PW" -H ldap://127.0.0.1 "$1" "$2"
}

if [ "$UNDO" = "1" ]; then
    modrdn "uid=${LOGIN}${SUFFIX},${PEOPLE_DN}" "uid=${LOGIN}"
    echo "учётка ${LOGIN} разблокирована"
else
    modrdn "uid=${LOGIN},${PEOPLE_DN}" "uid=${LOGIN}${SUFFIX}"
    echo "учётка ${LOGIN} заблокирована"
fi
