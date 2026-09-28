#!/bin/sh
# Резервные копии базы стенда (задачи 1.2, 1.9, 1.18; приёмка НФ-39, НФ-78).
#
# Живёт в контейнере backup из deploy/docker-compose.yml: образ тот же, что у db,
# потому что pg_basebackup обязан быть той же ветки, что и сервер. Устройство:
#
# - Раз в сутки в BACKUP_AT (02:00 по Москве) — физическая копия кластера
#   pg_basebackup в /backups/base/<время>/, tar со сжатием zstd, с манифестом;
#   сразу после — pg_verifybackup по этому манифесту.
# - Между копиями база сама кладёт каждый сегмент журнала (WAL) в /backups/wal
#   (archive_command в docker-compose.yml), не реже раза в 15 минут. Копия плюс
#   архив дают восстановление на любой момент после копии — потеря не больше
#   15 минут при норме НФ-39 в час.
# - Хранятся BACKUP_KEEP последних копий и архив WAL начиная с самой старой из них.
#   Сколько — по замеру журнала за сутки: deploy/wal-rate.sh, docs/restore.md,
#   «Сколько хранить».
# - Каждая попытка — строка в /backups/backup.log: время, итог, размер, секунды,
#   кто запустил. Отдельной таблицы нет: ни одна строка приёмки её не требует.
#
# Почему физическая копия, а не pg_dump. Восстановить на момент времени (1.18)
# можно только от физической копии: журнал накатывается на файлы кластера,
# а не на дамп. И разворот копии — это распаковка файлов, а pg_restore
# на 55 ГБ заново строил бы все индексы.
#
# Режимы (из каталога deploy):
#   docker compose exec backup sh /backup.sh            # копия сейчас, запуск=manual
#   docker compose exec backup sh /backup.sh --drill    # учебное восстановление
#   docker compose run --rm --no-deps -v moskollektor_pgdata:/var/lib/postgresql \
#     backup --restore <копия|latest> [<момент>]      # настоящее, docs/restore.md
#   --loop — то, что контейнер делает сам: ждёт BACKUP_AT и снимает копию.
set -eu

B=/backups
KEEP=${BACKUP_KEEP:-1}
AT=${BACKUP_AT:-02:00}
export PGHOST=${PGHOST:-db} PGUSER=${POSTGRES_USER:?} PGPASSWORD=${POSTGRES_PASSWORD:?}
DB=${POSTGRES_DB:?}

log() { echo "$(date -Iseconds) $*" >> "$B/backup.log"; echo "$*"; }

# Архив WAL пишет процесс postgres (uid 999) контейнера db, а том backups докер
# создаёт от root — без этой строки база не смогла бы положить ни сегмента.
prepare() {
  mkdir -p "$B/base" "$B/wal"
  chown postgres:postgres "$B/wal"
}

backup() {
  trigger=$1
  prepare
  name=$(date +%Y%m%dT%H%M%S)
  dir="$B/base/$name"
  start=$(date +%s)
  # Место до копии, а не по ходу. Том backups на стенде лежит на корневом диске
  # вместе с базой: не влезшая копия забивала его до 100 % и только потом падала
  # (проверено 28.09.2026 на томе в 700 МБ), а у базы на полном диске встаёт
  # журнал. Новая копия весит примерно как прошлая; плюс 10 % на рост базы и 2 ГБ,
  # чтобы после копии базе осталось место под pg_wal (max_wal_size = 1 ГБ).
  # Первую копию не проверяем: сравнить не с чем.
  prev=$(ls -1 "$B/base" | sort | tail -1)
  if [ -n "$prev" ]; then
    need=$(( $(du -sk "$B/base/$prev" | cut -f1) * 11 / 10 + 2097152 ))
    free=$(df -Pk "$B" | awk 'NR==2 {print $4}')
    if [ "$free" -lt "$need" ]; then
      log "СБОЙ $name запуск=$trigger места нет — нужно $(( need / 1024 )) МБ (прошлая копия плюс 10 % и 2 ГБ), свободно $(( free / 1024 )) МБ; docs/restore.md, «Сколько хранить»"
      return 1
    fi
  fi
  # -X stream: журнал, нужный самой копии, едет внутри неё (pg_wal.tar), и копия
  # поднимается даже без архива. -c fast: контрольная точка сразу, а не через
  # checkpoint_timeout. Сжимает сервер: по сети идёт уже сжатое.
  if ! out=$(pg_basebackup -D "$dir" -Ft -Z server-zstd -X stream -c fast 2>&1) \
     || ! out=$(pg_verifybackup -n "$dir" 2>&1); then
    rm -rf "$dir"
    log "СБОЙ $name запуск=$trigger $(printf '%s' "$out" | tail -1)"
    return 1
  fi
  # Первый сегмент журнала, нужный этой копии: от него и новее архив хранится.
  # Имя из backup_manifest («Start-LSN» диапазона WAL) переводит в имя файла сама база.
  lsn=$(sed -n 's/.*"Start-LSN": "\([^"]*\)".*/\1/p' "$dir/backup_manifest" | head -1)
  psql -d "$DB" -tAc "select pg_walfile_name('$lsn')" > "$dir/wal_start"
  size=$(du -sh "$dir" | cut -f1)
  log "ok $name размер=$size сек=$(( $(date +%s) - start )) запуск=$trigger"
  prune
}

prune() {
  # Копии — старше KEEP последних; архив — всё, что старше самой старой оставленной.
  ls -1 "$B/base" | sort | head -n -"$KEEP" | while read -r old; do
    rm -rf "${B:?}/base/$old"
    log "удалена $old: хранится $KEEP последних"
  done
  oldest=$(ls -1 "$B/base" | sort | head -1)
  [ -n "$oldest" ] && [ -s "$B/base/$oldest/wal_start" ] || return 0
  pg_archivecleanup -x .zst "$B/wal" "$(cat "$B/base/$oldest/wal_start")"
}

# Разворачивает копию в каталог кластера $1 и пишет настройки восстановления.
# Второй аргумент — копия (имя или latest), третий — момент, до которого
# накатывать журнал (пусто — весь архив до конца).
unpack() {
  pgdata=$1 name=$2 target=${3:-}
  [ "$name" = latest ] && name=$(ls -1 "$B/base" | sort | tail -1)
  src="$B/base/$name"
  [ -f "$src/base.tar.zst" ] || { echo "нет копии $src"; return 1; }
  if [ -n "$(ls -A "$pgdata" 2>/dev/null)" ]; then
    echo "каталог $pgdata не пуст — отказываюсь затирать. Разбор — docs/restore.md"
    return 1
  fi
  mkdir -p "$pgdata"
  zstd -q -d -c "$src/base.tar.zst" | tar -x -C "$pgdata" || return 1
  # Журнал внутри копии идёт по отдельному потоку и не сжимается: pg_wal.tar.
  mkdir -p "$pgdata/pg_wal"
  tar -x -C "$pgdata/pg_wal" -f "$src/pg_wal.tar" || return 1
  # Прежние настройки восстановления стираем. Прошлый разворот на момент оставил
  # их в postgresql.auto.conf живой базы, оттуда они уехали в каждую следующую
  # копию, и старый recovery_target_time ронял бы накат: «recovery ended before
  # configured recovery target was reached» (поймано учениями 27.09.2026).
  sed -i '/^restore_command\|^recovery_target/d' "$pgdata/postgresql.auto.conf"
  {
    echo "restore_command = 'zstd -q -d -f -o %p $B/wal/%f.zst'"
    [ -n "$target" ] && echo "recovery_target_time = '$target'"
    echo "recovery_target_action = 'promote'"
  } >> "$pgdata/postgresql.auto.conf"
  touch "$pgdata/recovery.signal"
  chown -R postgres:postgres "$pgdata"
  chmod 700 "$pgdata"
  echo "$name"
}

# Число строк в каждой таблице базы — отпечаток, по которому сверяем копию с живой базой.
fingerprint() {
  psql "$@" -d "$DB" -tA -F' ' -c "
    select n.nspname||'.'||c.relname,
           (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', n.nspname, c.relname), false, true, '')))[1]::text
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r','p') and not c.relispartition
      and n.nspname not in ('pg_catalog','information_schema','tiger','topology')
    order by 1"
}

drill_fail() {
  log "учения СБОЙ $1: $2 — $(grep -E 'FATAL|PANIC' "$B/drill.log" | tail -1)"
  d=${DRILL_DIR:-$B/drill}
  su postgres -c "pg_ctl -D $d -m immediate stop" >/dev/null 2>&1 || true
  rm -rf "$d"
}

# Учебное восстановление (1.9): последняя копия плюс весь архив разворачиваются
# во временный кластер рядом, поднимаются на порту 5499, и отпечаток сверяется
# с живой базой. Живую базу не трогаем. Места нужно столько же, сколько весит база.
drill() {
  # Каталог учений — DRILL_DIR, по умолчанию рядом с копиями. Места он просит
  # столько же, сколько весит база: если на диске стенда его нет, каталог
  # выносят на отдельный том (docs/restore.md, «Место на диске»).
  d=${DRILL_DIR:-$B/drill}
  rm -rf "$d"
  mkdir -p "$(dirname "$d")"
  # Сначала место, потом разворот: база в 55 ГБ, развёрнутая в 33 ГБ свободного
  # места стенда (замер 28.09.2026), забила бы диск живой базы вместе с её архивом.
  need=$(psql -d "$DB" -tAc "select (sum(pg_database_size(datname)) * 1.1)::bigint from pg_database")
  free=$(df -B1 --output=avail "$(dirname "$d")" | tail -1)
  if [ "$free" -lt "$need" ]; then
    log "учения СБОЙ: места нет — нужно $((need / 1073741824)) ГБ (база плюс 10 %), свободно $((free / 1073741824)) ГБ в $(dirname "$d"); docs/restore.md, «Место на диске»"
    return 1
  fi
  # Отпечаток живой базы — прямо перед тем, как выгнать в архив текущий сегмент:
  # так между сверяемыми состояниями проходят доли секунды, а не весь разворот.
  # Писать в базу в это время всё равно нельзя: worker и emulator-smvu пишут
  # каждую минуту, их останавливают до учений (docs/restore.md, «Учения»).
  fingerprint > "$B/drill.live"
  # Выгнать в архив текущий сегмент: иначе последние минуты до учений остались бы
  # только в живой базе, и сверка разошлась бы на них.
  psql -d "$DB" -tAc "select pg_switch_wal()" >/dev/null
  sleep 2
  t0=$(date +%s)
  # unpack зовём в if: под set -e его провал иначе оборвал бы скрипт молча,
  # без строки в журнале и с недоразвёрнутым каталогом на диске.
  if ! name=$(unpack "$d" latest); then
    drill_fail "$(ls -1 "$B/base" | sort | tail -1)" "копия не разворачивается"
    return 1
  fi
  : > "$B/drill.log"
  chown postgres "$B/drill.log"   # журнал сервера пишет postgres, а не root
  if ! su postgres -c "pg_ctl -D $d -o '-p 5499 -c listen_addresses= -c archive_mode=off -c unix_socket_directories=/tmp' -l $B/drill.log -w -t 14400 start" >/dev/null; then
    drill_fail "$name" "сервер не поднялся"
    return 1
  fi
  # Ждём конца наката: сервер выходит из режима восстановления, когда журнал кончился.
  # Умер посреди наката — это провал учений, а не повод ждать вечно.
  while [ "$(psql -h /tmp -p 5499 -d "$DB" -tAc 'select pg_is_in_recovery()' 2>/dev/null)" != f ]; do
    if ! su postgres -c "pg_ctl -D $d status" >/dev/null; then
      drill_fail "$name" "сервер умер посреди наката"
      return 1
    fi
    sleep 1
  done
  secs=$(( $(date +%s) - t0 ))
  last=$(grep -o 'last completed transaction was at log time [0-9: .+-]*' "$B/drill.log" | tail -1 | sed 's/.*log time //')
  fingerprint -h /tmp -p 5499 > "$B/drill.restored"
  su postgres -c "pg_ctl -D $d -m fast stop" >/dev/null
  rm -rf "$d"
  diff=$(diff "$B/drill.live" "$B/drill.restored" || true)
  tables=$(wc -l < "$B/drill.live")
  echo "копия $name развёрнута и журнал накатан за $secs с (норма НФ-39 — 4 ч = 14400 с)"
  echo "последняя транзакция в восстановленной базе: ${last:-не записана}"
  if [ -z "$diff" ]; then
    echo "отпечаток: $tables таблиц, число строк совпало во всех"
    log "учения ok $name сек=$secs таблиц=$tables расхождений=0"
  else
    echo "отпечаток: $tables таблиц, расхождения (< живая база, > копия):"
    printf '%s\n' "$diff" | grep '^[<>]'
    log "учения РАСХОЖДЕНИЕ $name сек=$secs таблиц=$tables"
    return 1
  fi
}

# Ждать до следующего BACKUP_AT по часам контейнера (TZ=Europe/Moscow).
loop() {
  prepare
  while :; do
    now=$(date +%s)
    next=$(date -d "today $AT" +%s)
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow $AT" +%s)
    sleep $(( next - now ))
    backup schedule || true
  done
}

# Выключатель (BACKUP_ENABLED=0 в deploy/.env): ни копий, ни учений, архив журнала
# база не пишет (archive-wal.sh). Контейнер остаётся жив и пишет, почему молчит.
# Восстановление из уже снятой копии работает и при выключенных копиях.
if [ "${BACKUP_ENABLED:-1}" != 1 ] && [ "${1:-}" != --restore ]; then
  msg="копии выключены: BACKUP_ENABLED=$BACKUP_ENABLED в deploy/.env; docs/restore.md, «Включить и выключить копии»"
  if [ "${1:-}" = --loop ]; then mkdir -p "$B"; log "$msg"; exec sleep infinity; fi
  echo "$msg" >&2
  exit 1
fi

case "${1:-}" in
  --loop) loop ;;
  --drill) drill ;;
  --restore) shift; unpack /var/lib/postgresql/18/docker "$@" ;;
  "") backup manual ;;
  *) echo "режимы: без аргументов, --loop, --drill, --restore <копия|latest> [<момент>]"; exit 2 ;;
esac
