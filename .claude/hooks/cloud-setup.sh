#!/bin/sh
# Ставит зависимости в облачной сессии (claude.ai/code). Подключён в
# .claude/settings.json как SessionStart. Облако стартует со свежего клона:
# нет ни .venv, ни frontend/node_modules, а без них format.sh молча
# ничего не форматирует, и не идут ни pytest, ни npm run build.
# На Маке CLAUDE_CODE_REMOTE не задан — скрипт сразу выходит.

[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
cd "$(dirname "$0")/../.." || exit 0

# python-ldap из backend/requirements.txt собирается из исходников и просит
# заголовки libldap и libsasl. Нет apt или прав — идём дальше без них.
if command -v apt-get >/dev/null 2>&1 && ! [ -f /usr/include/ldap.h ]; then
  apt-get install -y -qq libldap2-dev libsasl2-dev >/dev/null 2>&1 ||
    echo "cloud-setup: libldap2-dev не встал, python-ldap может не собраться" >&2
fi

[ -x .venv/bin/python ] || python3 -m venv .venv
# ruff той же версии, что в .venv на Маке: другая версия форматирует иначе.
.venv/bin/pip install -q ruff==0.16.8 -r backend/requirements-dev.txt >/dev/null ||
  echo "cloud-setup: ruff или pytest не встали" >&2
.venv/bin/pip install -q -r backend/requirements.txt >/dev/null ||
  echo "cloud-setup: backend/requirements.txt встал не целиком" >&2

[ -d frontend/node_modules ] ||
  (cd frontend && npm ci --no-audit --no-fund >/dev/null) ||
  echo "cloud-setup: npm ci во frontend/ упал" >&2

exit 0
