#!/bin/sh
# Замер: сколько журнала (WAL) база пишет за сутки и сколько он весит в архиве
# (задача 1.18). По этому числу выбирается BACKUP_KEEP — сколько копий кластера
# и суток архива хранить, docs/restore.md, раздел «Сколько хранить».
#
# Замеряет живую базу за окно и пересчитывает на сутки: сколько байт журнала
# прошло (pg_current_wal_lsn в начале и в конце), сколько сегментов ушло в архив
# и насколько вырос каталог архива на диске — это уже сжатые zstd байты. Потом
# берёт размер последней копии и свободное место на томе и печатает, сколько
# копий влезает в половину свободного места: вторая половина — под учения
# и под рост базы.
#
# Запуск из корня репозитория, окно в минутах (по умолчанию 60):
#   DB_CONTAINER=moskollektor-db-1 sh deploy/wal-rate.sh 60
# Окно берите не меньше часа и не на ночной копии (02:00): копия сама пишет журнал.
set -eu
: "${DB_CONTAINER:?задайте DB_CONTAINER, например moskollektor-db-1}"
MIN=${1:-60}

q() { docker exec "$DB_CONTAINER" sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""; }
du_wal() { docker exec "$DB_CONTAINER" sh -c 'du -sb /backups/wal | cut -f1'; }

lsn0=$(q "select pg_current_wal_lsn()")
arch0=$(q "select archived_count from pg_stat_archiver")
disk0=$(du_wal)
echo "окно $MIN мин, начало $(date '+%d.%m.%Y %H:%M'), LSN $lsn0"
sleep $((MIN * 60))
raw=$(q "select pg_wal_lsn_diff(pg_current_wal_lsn(), '$lsn0')::bigint")
segs=$(( $(q "select archived_count from pg_stat_archiver") - arch0 ))
disk=$(( $(du_wal) - disk0 ))

day() { echo $(( $1 * 1440 / MIN )); }
mb() { echo $(( $1 / 1048576 )); }
echo "журнал за окно: $(mb "$raw") МБ, сегментов в архив: $segs, архив на диске вырос на $(mb "$disk") МБ"
echo "за сутки: журнал $(mb "$(day "$raw")") МБ, сегментов $(day "$segs"), архив $(mb "$(day "$disk")") МБ"

copy=$(docker exec "$DB_CONTAINER" sh -c 'd=$(ls -1d /backups/base/*/ 2>/dev/null | sort | tail -1); [ -n "$d" ] && du -sb "$d" | cut -f1 || echo 0')
free=$(docker exec "$DB_CONTAINER" sh -c 'df -B1 --output=avail /backups | tail -1')
per=$(( copy + $(day "$disk") ))
echo "последняя копия $(mb "$copy") МБ, свободно на томе $(mb "$free") МБ"
if [ "$copy" -eq 0 ]; then
  echo "копий ещё нет — снимите одну (docker compose exec backup sh /backup.sh) и повторите замер"
  exit 0
fi
# Хранится K копий и архив от старшей: K копий плюс K суток архива.
keep=$(( free / 2 / per ))
echo "сутки хранения стоят $(mb "$per") МБ (копия + архив за сутки); в половину свободного места влезает BACKUP_KEEP=$keep"
