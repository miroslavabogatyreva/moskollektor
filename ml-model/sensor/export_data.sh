#!/usr/bin/env bash
# Выгружает из базы стенда шесть CSV, на которых обучаются правила датчика.
# Запуск из корня репозитория на машине, где поднят deploy/docker-compose.yml
# и залита выгрузка заказчика:
#     bash ml-model/sensor/export_data.sh
# Другой контейнер базы: DB_CONTAINER=имя bash ml-model/sensor/export_data.sh
set -euo pipefail

out="$(dirname "$0")/data"
mkdir -p "$out"
db="${DB_CONTAINER:-moskollektor-db-1}"

q() {
  docker exec -i "$db" psql -U moskollektor -d moskollektor -q \
    <<<"\\copy ($2) to stdout with csv header" > "$out/$1.csv"
  echo "$1.csv: $(($(wc -l < "$out/$1.csv") - 1)) строк"
}

q channels    "select channel_id, system_kind, sensor_kind, tag, name, collector, picket, section_id, object_id, is_active, is_stub from smvu.channel order by channel_id"
q object_tree "select object_id, level, parent_id, kind, name from smvu.object_tree order by object_id"
q failures    "select episode_id, channel_id, section_id, started_at, ended_at, fault_value from smvu.model_failure_event order by started_at, channel_id"
q ppr_windows "select plan_row, object_id, match, sensor_kind, qty, dismantle_from, return_to, accepted_on from maint.ppr_window order by id"
q passports   "select e.source_key::int as channel_id, e.equipment_no, k.code as object_kind, e.model_no, e.build_year, e.in_service_from, e.service_life_years from asset.equipment e left join ref.object_kind k on k.id=e.object_kind_id where e.source_system='synthetic-demo' order by 1"
q checks      "select e.source_key::int as channel_id, mp.point_no, c.code as characteristic, mp.is_counter, m.measured_at, m.value_num, m.value_text from asset.measurement m join asset.measuring_point mp on mp.id=m.point_id join asset.equipment e on e.id=mp.equipment_id left join ref.characteristic c on c.id=mp.characteristic_id where e.source_system='synthetic-demo' order by 1, m.measured_at"
