#!/bin/sh
# Демо-стенд открывается с чужой машины без нашего участия (задача 1.10, MOS-109).
#
# Проверка смотрит на стенд глазами комиссии: с любой машины, одним curl,
# без -k и без правки /etc/hosts. Пять условий:
# 1. Сертификат проверяется обычным списком удостоверяющих центров — браузер
#    не покажет предупреждения, которое комиссия примет за поломку.
# 2. Корень отдаёт приложение, а не заглушку «Стенд поднят».
# 3. Экран входа подсказывает четыре демо-учётки (GET /api/auth/info).
# 4. Каждая из четырёх входит своим паролем.
# 5. Заголовок X-User-Login без пароля не пускает: демо открыто всему интернету,
#    и доверие к заголовку (AUTH_TRUST_HEADER=1 нашего стенда) сделало бы
#    администратором любого, кто его пришлёт.
#
# Запуск:  sh deploy/check-demo.sh https://135-106-216-101.sslip.io:8443
# DEMO_CA=<файл> — свой удостоверяющий центр вместо системного, только для
# прогона в песочнице, где публичного сертификата не выпустить.
set -u
URL=${1:?адрес демо-стенда, например https://135-106-216-101.sslip.io:8443}
CA=${DEMO_CA:+--cacert $DEMO_CA}
fail=0
ok() { echo "OK    $1"; }
bad() { echo "СБОЙ  $1"; fail=1; }

# shellcheck disable=SC2086
if out=$(curl -sS $CA -o /dev/null "$URL/" 2>&1); then
  ok "сертификат проверяется без -k"
else
  bad "сертификат не проверяется: $out"
fi
k="-k"  # дальше смотрим содержимое, даже если сертификат не прошёл

page=$(curl -s $k "$URL/")
if printf '%s' "$page" | grep -q 'index-' && ! printf '%s' "$page" | grep -q 'Москоллектор — стенд'; then
  ok "корень отдаёт приложение"
else
  bad "корень отдаёт не приложение"
fi

info=$(curl -s $k "$URL/api/auth/info")
n=$(printf '%s' "$info" | grep -o '"login":"[^"]*","password":"[^"]*"' | wc -l)
if [ "$n" -eq 4 ]; then
  ok "экран входа подсказывает 4 демо-учётки"
else
  bad "экран входа подсказывает $n демо-учёток из 4 — AUTH_DEMO_HINTS=1?"
fi
printf '%s' "$info" | grep -o '"login":"[^"]*","password":"[^"]*"' | sed 's/"login":"\([^"]*\)","password":"\([^"]*\)"/\1 \2/' |
while read -r login pass; do
  code=$(curl -s $k -o /dev/null -w '%{http_code}' -H 'content-type: application/json' \
    -d "{\"login\":\"$login\",\"password\":\"$pass\"}" "$URL/api/auth/login")
  if [ "$code" = 200 ]; then ok "вход $login по паролю"; else echo "СБОЙ  вход $login по паролю: $code"; echo x > "${TMPDIR:-/tmp}/check-demo.$$"; fi
done
[ -f "${TMPDIR:-/tmp}/check-demo.$$" ] && { fail=1; rm -f "${TMPDIR:-/tmp}/check-demo.$$"; }

code=$(curl -s $k -o /dev/null -w '%{http_code}' -H 'X-User-Login: admin1' "$URL/api/risks")
if [ "$code" = 401 ]; then
  ok "заголовок X-User-Login без пароля не пускает: 401"
else
  bad "заголовок X-User-Login: admin1 без пароля дал $code — AUTH_TRUST_HEADER=0?"
fi

exit $fail
