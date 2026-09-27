#!/bin/sh
# Ставит зависимости в облачной сессии (claude.ai/code). Подключён в
# .claude/settings.json как SessionStart. Облако стартует со свежего клона:
# нет ни .venv, ни frontend/node_modules, а без них format.sh молча
# ничего не форматирует, и не идут ни pytest, ни npm run build.
# На Маке CLAUDE_CODE_REMOTE не задан — скрипт сразу выходит.

[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
cd "$(dirname "$0")/../.." || exit 0

# Предупреждения пишем в stdout, а не в stderr: stdout хука SessionStart
# Claude получает в контекст, stderr — нет.

# python-ldap из backend/requirements.txt собирается из исходников и просит
# заголовки libldap и libsasl. В свежем контейнере apt не знает пакетов,
# пока не сделан apt-get update.
if command -v apt-get >/dev/null 2>&1 && ! [ -f /usr/include/ldap.h ]; then
  { apt-get update -qq && apt-get install -y -qq libldap2-dev libsasl2-dev; } >/dev/null 2>&1 ||
    echo "cloud-setup: libldap2-dev не встал, python-ldap может не собраться"
fi

[ -x .venv/bin/python ] || python3 -m venv .venv
# ruff той же версии, что в .venv на Маке: другая версия форматирует иначе.
.venv/bin/pip install -q ruff==0.16.8 -r backend/requirements-dev.txt >/dev/null 2>&1 ||
  echo "cloud-setup: ruff или pytest не встали"
# pip ставит список целиком или ничего: не собрался python-ldap — не встают
# и fastapi с asyncpg. Тогда ставим всё без него, тесты без ldap пойдут.
if ! .venv/bin/pip install -q -r backend/requirements.txt >/dev/null 2>&1; then
  grep -v '^python-ldap' backend/requirements.txt |
    .venv/bin/pip install -q -r /dev/stdin >/dev/null 2>&1 &&
    echo "cloud-setup: python-ldap не собрался, остальной бэкенд стоит" ||
    echo "cloud-setup: backend/requirements.txt не встал"
fi

[ -d frontend/node_modules ] ||
  (cd frontend && npm ci --no-audit --no-fund >/dev/null 2>&1) ||
  echo "cloud-setup: npm ci во frontend/ упал"

exit 0
