#!/bin/sh
# Выкладка стенда из origin/master. Сам по себе запускается GitHub Actions после
# пуша в master (.github/workflows/deploy.yml): ключ Actions в authorized_keys
# сервера умеет вызывать только этот файл. Руками — то же самое:
#   ssh root@135.106.216.101 /srv/moskollektor/deploy/deploy.sh
#
# ВСЕГДА ВЕРШИНА origin/master, аргументов нет. Стенд отвечает за то, что лежит
# в git, а не за то, что оказалось на чьём-то ноутбуке. Откат — revert в master
# и пуш, а не выкладка старого коммита мимо git.
#
# ПОЧЕМУ ДВЕ СТАДИИ. git reset переписывает и этот файл, а sh читает скрипт
# по ходу исполнения. Поэтому первая стадия только обновляет дерево и передаёт
# управление новой версии скрипта через exec — правка deploy.sh действует
# с той же выкладки, которой она приехала.
#
# ФРОНТ И ЗАГЛУШКА — РАЗНЫЕ КАТАЛОГИ (задача 1.11, MOS-246). Собранный фронт едет
# в deploy/nginx/app, которого нет в git, поэтому reset его не трогает. Заглушка
# «Стенд поднят» остаётся в deploy/nginx/html, в git, и nginx отдаёт её, только
# пока app пуст. До 27.09.2026 оба жили в html, и reset клал заглушку поверх.
set -eu
cd "$(dirname "$0")/.."

if [ -z "${DEPLOY_STAGE2:-}" ]; then
  exec 9>/var/lock/moskollektor-deploy.lock
  flock 9   # две выкладки подряд встают в очередь, а не мешают друг другу
  old=$(git rev-parse -q --verify HEAD || echo none)
  git fetch -q origin master
  git reset -q --hard origin/master
  DEPLOY_STAGE2=1 DEPLOY_OLD=$old exec sh deploy/deploy.sh
fi

new=$(git rev-parse HEAD)
echo "выкладка $DEPLOY_OLD -> $new"

# Фронт. node на сервере нет, собираем в контейнере той же версии, что у нас.
docker run --rm -v "$PWD":/src -w /src/frontend node:26-alpine \
  sh -c 'npm ci --no-audit --no-fund --loglevel=error && npm run build'
mkdir -p deploy/nginx/app
rsync -a --delete-after frontend/dist/ deploy/nginx/app/
echo "$new" > deploy/nginx/app/version.txt

# Бэкенд. migrate собираем вместе с api: миграции вшиты в образ (пятая ловушка
# в docs/server.md). Неизменённый образ compose не пересоздаёт.
cd deploy
# db пересоздаётся, только если в compose сменились его настройки (так приехал
# свой pg_hba.conf, задача 1.6), иначе ничего не делает. --wait — ждать healthy:
# migrate ниже без живой базы упадёт.
docker compose up -d --wait db
docker compose --profile app build -q migrate api worker emulator-smvu
docker compose --profile app run --rm migrate
docker compose --profile app up -d api worker emulator-smvu

# up -d пересоздаёт nginx, если в compose сменились его тома (так приехал каталог
# nginx/app), и ничего не делает, если не сменились. nginx.conf смонтирован файлом
# и держится за старый inode — после его правки нужен ещё и перезапуск, reload
# не поможет (вторая ловушка в docs/server.md).
docker compose up -d nginx
if [ "$DEPLOY_OLD" = none ] || ! git diff --quiet "$DEPLOY_OLD" "$new" -- nginx/; then
  docker compose restart nginx
fi

api=$(docker compose ps -q api)
for _ in $(seq 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$api")
  [ "$state" = healthy ] && break
  sleep 2
done
if [ "$state" != healthy ]; then
  echo "api не поднялся за 2 минуты: $state"
  docker compose logs --tail 50 api
  exit 1
fi
[ "$(curl -sk https://127.0.0.1/version.txt)" = "$new" ]
echo "готово: $new"
