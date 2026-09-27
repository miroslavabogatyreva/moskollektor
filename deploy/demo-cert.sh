#!/bin/sh
# Сертификат Let's Encrypt для демо-стенда (задача 1.10, MOS-109).
#
# Самоподписанный сертификат основного стенда браузер комиссии встретит
# предупреждением, и его примут за поломку. Для демо выпускаем настоящий
# на имя DEMO_HOST из demo.env (по умолчанию 135-106-216-101.sslip.io — это имя
# само указывает на адрес стенда, домен покупать не нужно).
#
# Как это работает. certbot из своего образа кладёт проверочный файл
# в deploy/nginx/acme, удостоверяющий центр забирает его по порту 80, а порт 80
# держит nginx ОСНОВНОГО стенда — у него для этого есть location
# /.well-known/acme-challenge/ (deploy/nginx/nginx.conf). Потом скрипт копирует
# сертификат в CERTS_DIR демо и перезапускает nginx демо.
#
# Запуск на сервере, из каталога deploy, после того как демо поднято:
#   sh demo-cert.sh
# Сертификат живёт 90 суток, до защиты хватает; продлить — тот же запуск.
set -eu
cd "$(dirname "$0")"
[ -f demo.env ] || { echo "нет deploy/demo.env — скопируйте demo.env.example"; exit 1; }
# shellcheck disable=SC1091
. ./demo.env
: "${DEMO_HOST:?задайте DEMO_HOST в demo.env}"
: "${CERTS_DIR:?задайте CERTS_DIR в demo.env}"

mkdir -p nginx/acme nginx/letsencrypt "$CERTS_DIR"
docker run --rm \
  -v "$PWD/nginx/acme:/var/www/acme" \
  -v "$PWD/nginx/letsencrypt:/etc/letsencrypt" \
  certbot/certbot certonly --webroot -w /var/www/acme -d "$DEMO_HOST" \
  --agree-tos --register-unsafely-without-email --non-interactive --keep-until-expiring

cp "nginx/letsencrypt/live/$DEMO_HOST/fullchain.pem" "$CERTS_DIR/server.crt"
cp "nginx/letsencrypt/live/$DEMO_HOST/privkey.pem" "$CERTS_DIR/server.key"
chmod 600 "$CERTS_DIR/server.key"
docker compose -p moskollektor-demo --env-file demo.env restart nginx
echo "сертификат на $DEMO_HOST в $CERTS_DIR; проверка: sh check-demo.sh https://$DEMO_HOST:${HTTPS_PORT:-443}"
