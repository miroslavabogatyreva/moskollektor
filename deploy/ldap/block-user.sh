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

compose_exec() {
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" --project-directory "${COMPOSE_DIR}" \
        --profile ldap exec -T ldap "$@"
}

# Идемпотентность (находка 92): перед modrdn смотрим, где DN сейчас стоит,
# а не бьём наугад — повторный block/--undo не должен падать кодом 32.
exists() {
    compose_exec ldapsearch -x -D "$ADMIN_DN" -w "$ADMIN_PW" -H ldap://127.0.0.1 \
        -b "$1" -s base dn >/dev/null 2>&1
}

# -r (deleteoldrdn): без него старое значение uid остаётся на записи вторым
# значением атрибута uid — заблокированная запись отзывалась бы на
# (uid=<login>) поиском, хотя bind по старому DN уже не проходит (находка
# оркестратора 24.09.2026).
modrdn() {
    compose_exec ldapmodrdn -r -x -D "$ADMIN_DN" -w "$ADMIN_PW" -H ldap://127.0.0.1 "$1" "$2"
}

ACTIVE_DN="uid=${LOGIN},${PEOPLE_DN}"
BLOCKED_DN="uid=${LOGIN}${SUFFIX},${PEOPLE_DN}"

if [ "$UNDO" = "1" ]; then
    if exists "$ACTIVE_DN"; then
        echo "учётка ${LOGIN} уже активна"
    elif exists "$BLOCKED_DN"; then
        modrdn "$BLOCKED_DN" "uid=${LOGIN}"
        echo "учётка ${LOGIN} разблокирована"
    else
        echo "учётка ${LOGIN} не найдена ни активной, ни заблокированной" >&2
        exit 1
    fi
else
    if exists "$BLOCKED_DN"; then
        echo "учётка ${LOGIN} уже заблокирована"
    elif exists "$ACTIVE_DN"; then
        modrdn "$ACTIVE_DN" "uid=${LOGIN}${SUFFIX}"
        echo "учётка ${LOGIN} заблокирована"
    else
        echo "учётка ${LOGIN} не найдена ни активной, ни заблокированной" >&2
        exit 1
    fi
fi
