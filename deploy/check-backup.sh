#!/bin/sh
# Резервные копии работают сами (задачи 1.2 и 1.18, приёмка НФ-78 и НФ-39).
#
# Что проверяем на живой базе, а не по конфигу:
# 1. Архив журналов (WAL) включён и идёт: последний сегмент ушёл в архив не раньше
#    часа назад, и после него не было неудачи. Отсюда потеря данных не больше часа.
# 2. Журнал копий /backups/backup.log: последняя удачная копия моложе 26 часов
#    (сутки плюс запас на саму копию), и её сделало расписание, а не человек.
#    Семь последних строк печатаем — это и есть «журнал заданий за семь суток».
#
# Запуск из корня репозитория, контейнер базы — по имени:
#   DB_CONTAINER=moskollektor-db-1 sh deploy/check-backup.sh
# Стенд с другой машины — DOCKER_HOST=ssh://root@СЕРВЕР, как у check-db-access.sh.
set -u
: "${DB_CONTAINER:?задайте DB_CONTAINER, например moskollektor-db-1}"
fail=0

q() { docker exec "$DB_CONTAINER" sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\"" 2>&1; }

mode=$(q "show archive_mode")
if [ "$mode" != on ]; then
  echo "СБОЙ  архив WAL: archive_mode = $mode, журналы не копируются"
  fail=1
else
  # Возраст последнего сегмента в архиве и была ли неудача ПОСЛЕ него.
  row=$(q "select coalesce(extract(epoch from now() - last_archived_time)::int, -1),
                  coalesce(last_failed_time > last_archived_time, false), last_archived_wal
           from pg_stat_archiver")
  age=$(printf '%s' "$row" | cut -d'|' -f1)
  failed=$(printf '%s' "$row" | cut -d'|' -f2)
  wal=$(printf '%s' "$row" | cut -d'|' -f3)
  if [ "$age" -lt 0 ] 2>/dev/null; then
    echo "СБОЙ  архив WAL: ни одного сегмента в архиве"
    fail=1
  elif [ "$failed" = t ]; then
    echo "СБОЙ  архив WAL: после $wal копирование падает — docker logs $DB_CONTAINER"
    fail=1
  elif [ "$age" -gt 3600 ]; then
    echo "СБОЙ  архив WAL: последний сегмент $wal ушёл $age с назад, больше часа"
    fail=1
  else
    echo "OK    архив WAL: последний сегмент $wal ушёл $age с назад"
  fi
fi

log=$(docker exec "$DB_CONTAINER" sh -c 'tail -n 7 /backups/backup.log' 2>/dev/null)
# Последнюю копию ищем по всему журналу, а не в семи строках для показа: пять
# учений подряд выталкивали строку копии из хвоста, и проверка писала «нет ни
# одной удачной копии» при свежей копии (27.09.2026). Журнал — строка на попытку.
# Только строки копий: у строки учений между временем и ok стоит слово «учения».
last=$(docker exec "$DB_CONTAINER" sh -c "grep '^[^ ]* ok ' /backups/backup.log" 2>/dev/null | tail -1)
if [ -z "$last" ]; then
  echo "СБОЙ  журнал копий: в /backups/backup.log нет ни одной удачной копии"
  fail=1
else
  # Строка: <время ISO> ok <имя> размер=… сек=… запуск=<schedule|manual>
  ts=$(printf '%s' "$last" | cut -d' ' -f1)
  age=$(( $(date +%s) - $(date -d "$ts" +%s) ))
  if [ "$age" -gt 93600 ]; then
    echo "СБОЙ  журнал копий: последняя удачная копия $ts, $age с назад — больше 26 часов"
    fail=1
  elif ! printf '%s' "$last" | grep -q 'запуск=schedule'; then
    echo "СБОЙ  журнал копий: последнюю копию запустили руками, а не расписание: $last"
    fail=1
  else
    echo "OK    журнал копий: последняя удачная $ts, $age с назад, по расписанию"
  fi
  printf '%s\n' "$log" | sed 's/^/        /'
fi

exit $fail
