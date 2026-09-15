#!/bin/sh
# Самоподписанный сертификат для стенда: домена и удостоверяющего центра у нас нет.
# Проверке НФ-75 это не мешает — nmap и openssl читают версии протокола и наборы
# шифров независимо от того, кто подписал сертификат.
#
# Запускать из каталога deploy:  sh make-cert.sh
set -eu

CERTS="$(dirname "$0")/nginx/certs"
mkdir -p "$CERTS"

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$CERTS/server.key" \
    -out    "$CERTS/server.crt" \
    -subj   "/C=RU/O=Moskollektor/CN=moskollektor.local" \
    -addext "subjectAltName=DNS:moskollektor.local,DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERTS/server.key"
echo "Сертификат и ключ лежат в $CERTS — в git они не идут."
