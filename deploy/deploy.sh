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
# ЗАГЛУШКА В deploy/nginx/html. В git там лежит index.html «Стенд поднят» (задача
# 1.1), а на стенде по тому же пути собранный фронт — reset кладёт заглушку поверх
# (ловушка задачи плана 1.11). Поэтому вторая стадия первым делом возвращает
# прошлую сборку из frontend/dist: git её не отслеживает, она переживает reset.
# sparse-checkout не годится: он удаляет файл из дерева, и сайт падает совсем.
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

[ -d frontend/dist ] && rsync -a frontend/dist/ deploy/nginx/html/
new=$(git rev-parse HEAD)
echo "выкладка $DEPLOY_OLD -> $new"

# Фронт. node на сервере нет, собираем в контейнере той же версии, что у нас.
docker run --rm -v "$PWD":/src -w /src/frontend node:26-alpine \
  sh -c 'npm ci --no-audit --no-fund --loglevel=error && npm run build'
rsync -a --delete-after frontend/dist/ deploy/nginx/html/
echo "$new" > deploy/nginx/html/version.txt

# Бэкенд. migrate собираем вместе с api: миграции вшиты в образ (пятая ловушка
# в docs/server.md). Неизменённый образ compose не пересоздаёт.
cd deploy
docker compose --profile app build -q migrate api worker emulator-smvu
docker compose --profile app run --rm migrate
docker compose --profile app up -d api worker emulator-smvu

# nginx.conf смонтирован файлом и держится за старый inode — после его правки
# nginx нужен перезапуск, reload не поможет (вторая ловушка в docs/server.md).
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
