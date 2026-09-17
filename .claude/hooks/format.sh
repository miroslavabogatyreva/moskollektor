#!/bin/sh
# Форматирует файл сразу после правки. Подключён в .claude/settings.local.json
# как PostToolUse на Edit|Write|MultiEdit. Claude Code подаёт на stdin JSON,
# путь к файлу лежит в .tool_input.file_path.
#
# Python форматирует ruff из .venv, фронт — prettier из frontend/node_modules.
# Нет ни того, ни другого — скрипт молча выходит, правка не ломается.
# Документы (.md) и данные (.json) не трогаем: в docs/ вёрстка ручная,
# а frontend/public/data/sections.json на 220 КБ prettier только раздует.

root=$(cd "$(dirname "$0")/../.." && pwd)

f=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
[ -n "$f" ] && [ -f "$f" ] || exit 0

# Файл вне репозитория (скратчпад, чужой проект) не наше дело.
case "$f" in "$root"/*) ;; *) exit 0 ;; esac

case "$f" in
  *.py)
    ruff="$root/.venv/bin/ruff"
    [ -x "$ruff" ] || exit 0
    # 43 файла из 50 писались с ручным выравниванием: ключ на строке, русское
    # объяснение под ним. Ruff это схлопывает в строку на 120 символов.
    # Поэтому трогаем только два вида файлов: те, в которых ruff ничего
    # не меняет, и те, которых ещё нет в git, — то есть новый код.
    if git -C "$root" ls-files --error-unmatch "$f" >/dev/null 2>&1 &&
       ! "$ruff" format --check "$f" >/dev/null 2>&1; then
      exit 0
    fi
    "$ruff" format -q "$f" >/dev/null 2>&1
    ;;
  *.ts|*.tsx|*.css)
    p="$root/frontend/node_modules/.bin/prettier"
    [ -x "$p" ] || exit 0
    "$p" --write --log-level silent "$f" >/dev/null 2>&1
    ;;
esac

exit 0
