#!/usr/bin/env bash
# pack.sh — пакет сдачи: архив, который эксперты распаковывают и поднимают
# по docs/install.md, не заглядывая в репозиторий (MOS-125, план 9.13, НФ-88).
#
# Заказчик 19.09.2026: «Формат сдачи - это пакет, который можно распаковать
# и запустить, с обязательной инструкцией по развертыванию».
#
# Запуск из любого каталога:
#   bash delivery/pack.sh            # собрать dist/moskollektor-<версия>.tar.gz
#   bash delivery/pack.sh --check    # собрать и проверить распаковкой в пустой каталог
#
# Архив собирает git archive из закоммиченного HEAD, а не из рабочего каталога:
# в пакет попадает ровно то, что лежит в git под этим коммитом, без dataset/,
# .venv/, node_modules/ и deploy/.env. Незакоммиченные правки скрипт не везёт
# и поэтому отказывается собирать, пока они есть.
#
# Не едут три каталога. final-presentation/ — 68 МБ, презентация сдаётся своей
# ссылкой (docs/delivery.md). .agents/ и .claude/ — настройки Claude Code
# для разработки, продукту и экспертам не нужны. Выгрузки заказчика в пакете
# нет: она у заказчика своя, а заливку описывает docs/install.md разд. 5.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "СБОЙ: в рабочем каталоге незакоммиченные правки — пакет соберётся из HEAD без них."
  echo "      Закоммитьте или уберите их и повторите."
  exit 1
fi

ver="$(git describe --tags --always)"
name="moskollektor-$ver"
mkdir -p dist
out="dist/$name.tar.gz"

git archive --format=tar.gz --prefix="$name/" -o "$out" HEAD -- . \
  ':(exclude)final-presentation' ':(exclude).agents' ':(exclude).claude'

bytes=$(wc -c < "$out")
echo "пакет: $out, $((bytes / 1024 / 1024)) МБ ($bytes байт), коммит $(git rev-parse --short HEAD)"

[ "${1:-}" = "--check" ] || exit 0

# Проверка распаковкой: пустой каталог, в нём только архив. Смотрим то, без чего
# эксперт не поднимет стенд по инструкции, — файлы на месте, compose читается,
# миграции сходятся. Поднять сам стенд здесь нельзя: нужен Docker и выгрузка.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
tar -xzf "$out" -C "$tmp"
root="$tmp/$name"
fail=0

for f in docs/install.md docs/build.md docs/architecture.md docs/data-processing.md \
         docs/unmet-requirements.md deploy/docker-compose.yml deploy/.env.example \
         backend/Dockerfile backend/requirements.txt ml-stub/Dockerfile \
         frontend/package.json frontend/package-lock.json db/migrations db/seed contracts; do
  if [ -e "$root/$f" ]; then echo "OK    есть $f"; else echo "СБОЙ  нет $f"; fail=1; fi
done

for f in dataset final-presentation deploy/.env; do
  if [ -e "$root/$f" ]; then echo "СБОЙ  лишнее в пакете: $f"; fail=1; else echo "OK    нет $f"; fi
done

if (cd "$root" && python3 code/check_schema.py > "$tmp/schema.log" 2>&1); then
  echo "OK    миграции: $(tail -1 "$tmp/schema.log")"
else
  echo "СБОЙ  миграции:"; tail -5 "$tmp/schema.log"; fail=1
fi

# docker compose config разбирает файл и подстановки без демона Docker.
if docker compose version > /dev/null 2>&1; then
  # Два секрета compose требует обязательно (${VAR:?}); эксперт задаёт их сам
  # по docs/install.md разд. 4, здесь подставляем заглушки только для разбора.
  sed -e 's/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=check/' -e 's/^AUTH_SECRET=$/AUTH_SECRET=check/' \
    "$root/deploy/.env.example" > "$root/deploy/.env"
  if (cd "$root/deploy" && docker compose --profile app --profile ldap config -q 2> "$tmp/compose.log"); then
    echo "OK    docker compose config: файл читается со всеми профилями"
  else
    echo "СБОЙ  docker compose config:"; head -5 "$tmp/compose.log"; fail=1
  fi
else
  echo "ПРОПУСК docker compose config: нет docker compose"
fi

[ "$fail" = 0 ] && echo "итого: пакет распаковывается и полон" || echo "итого: СБОЙ"
exit "$fail"
