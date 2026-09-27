#!/bin/sh
# Два замечания приёмки 1.1 про доступ к базе (задача 1.6, MOS-82).
#
# 1. Заглушка пароля не должна работать. Если deploy/.env скопирован из образца
#    и не заполнен, compose обязан отказаться поднимать стенд и назвать
#    переменную, а не поднять базу с паролем, который знает весь git.
# 2. База не пускает по неверному паролю и изнутри своего контейнера. initdb
#    пишет в pg_hba.conf `host all all 127.0.0.1/32 trust`, и 15.09.2026 приёмка
#    зашла с паролем «неверный» — у получившего шелл в контейнере был полный доступ.
#
# Запуск из корня репозитория:
#   sh deploy/check-db-access.sh                          # только п. 1, стенд не нужен
#   DB_CONTAINER=moskollektor-db-1 sh deploy/check-db-access.sh
#   API_CONTAINER=moskollektor-api-1 …  — ещё и ключ подписи куки AUTH_SECRET у api
# Стенд с другой машины — тот же DOCKER_HOST=ssh://root@СЕРВЕР, что у строки
# «лицензии: сборка» в delivery/check-all.sh.
set -u
cd "$(dirname "$0")"
fail=0

for var in POSTGRES_PASSWORD AUTH_SECRET; do
  # Остальные переменные берём из образца, эту одну — пустой, как в образце,
  # и отдельно вторую: compose называет только первую пустую.
  if [ "$var" = POSTGRES_PASSWORD ]; then other=AUTH_SECRET; else other=POSTGRES_PASSWORD; fi
  out=$(env "$other=x" docker compose --env-file .env.example --profile app config -q 2>&1)
  if [ $? -ne 0 ] && printf '%s' "$out" | grep -q "$var"; then
    echo "OK    образец .env: compose отказывается без $var"
  else
    echo "СБОЙ  образец .env: compose поднимает стенд без своего $var: ${out:-код 0}"
    fail=1
  fi
done

if [ -n "${DB_CONTAINER:-}" ]; then
  for host in 127.0.0.1 ::1; do
    # ::1 бывает недоступен вовсе (контейнер без IPv6) — это не дыра, а отсутствие
    # входа; считаем проверкой только ответ самого сервера.
    out=$(docker exec -e PGPASSWORD=неверный -e PGCONNECT_TIMEOUT=5 "$DB_CONTAINER" \
      sh -c "psql -h $host -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc 'select 1'" 2>&1)
    if printf '%s' "$out" | grep -q "password authentication failed"; then
      echo "OK    $host в контейнере: неверный пароль отклонён"
    elif [ "$out" = 1 ]; then
      echo "СБОЙ  $host в контейнере: неверный пароль пустил"
      fail=1
    else
      # Контейнер без IPv6 отвечает на ::1 пустым «psql: error:» — входа нет вовсе.
      echo "ПРОПУСК $host в контейнере: сервер не ответил ($(printf '%s' "$out" | head -1))"
    fi
  done
  # Третье замечание приёмки: пароль на сервере сгенерирован, а не вписан руками
  # из старого образца. openssl rand -hex 16 даёт 32 знака; меньше 16 — не он.
  out=$(docker exec "$DB_CONTAINER" sh -c 'printf "%s" "$POSTGRES_PASSWORD" | wc -c; [ "$POSTGRES_PASSWORD" = "СМЕНИ-МЕНЯ" ] && echo заглушка')
  if printf '%s' "$out" | grep -q заглушка || [ "$(printf '%s' "$out" | head -1)" -lt 16 ]; then
    echo "СБОЙ  пароль базы в контейнере не сгенерирован: $(printf '%s' "$out" | tr '\n' ' ')"
    fail=1
  else
    echo "OK    пароль базы в контейнере: знаков $(printf "%s" "$out" | head -1), не заглушка"
  fi
  # Контроль с другой стороны: верный пароль пускает. Без него строка выше
  # зеленела бы и на базе, которая не пускает никого.
  out=$(docker exec "$DB_CONTAINER" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select 1"' 2>&1)
  if [ "$out" = 1 ]; then
    echo "OK    127.0.0.1 в контейнере: верный пароль пускает"
  else
    echo "СБОЙ  127.0.0.1 в контейнере: верный пароль не пускает: $out"
    fail=1
  fi
fi

# Ключ подписи куки входа в живом контейнере api (замечание проверки 27.09.2026).
# Заглушка «СМЕНИ-МЕНЯ» из старого образца не роняет api: куки подписываются
# ключом, который знает всякий, кто читал git, и подделать вход может любой.
# openssl rand -hex 32 даёт 64 знака; меньше 32 — не он.
if [ -n "${API_CONTAINER:-}" ]; then
  out=$(docker exec "$API_CONTAINER" sh -c 'printf "%s" "$AUTH_SECRET" | wc -c; [ "$AUTH_SECRET" = "СМЕНИ-МЕНЯ" ] && echo заглушка' 2>&1)
  if printf '%s' "$out" | grep -q заглушка || [ "$(printf '%s' "$out" | head -1)" -lt 32 ] 2>/dev/null; then
    echo "СБОЙ  AUTH_SECRET в контейнере не сгенерирован: $(printf '%s' "$out" | tr '\n' ' ')"
    fail=1
  elif ! [ "$(printf '%s' "$out" | head -1)" -ge 32 ] 2>/dev/null; then
    echo "СБОЙ  AUTH_SECRET в контейнере не прочитан: $out"
    fail=1
  else
    echo "OK    AUTH_SECRET в контейнере: знаков $(printf '%s' "$out" | head -1), не заглушка"
  fi
fi

exit $fail
