#!/usr/bin/env bash
# examples.sh — примеры вызовов API. Задача MOS-46 (Q4.9), приёмка М-17.
#
# GET /docs и /openapi.json генерирует FastAPI сам (backend/app/api/main.py) —
# наша работа здесь только собрать примеры и держать их в согласии с настоящими
# ответами, а не выдумать форму заранее (М-17: «примеры совпадают с фактическими
# ответами»). Поэтому файл не текст с примерами, а исполняемая сверка: каждый
# вызов реально проверен 16.09.2026 против процесса app.api.main, поднятого на
# тестовых учётках dispatcher1/admin1 (db/seed/rbac.sql) через туннель к боевой
# базе, и код ниже сверяет то же самое при каждом запуске, а не только один раз.
#
# Покрыты только ОПУБЛИКОВАННЫЕ методы — М-17 требует описать именно их, не весь
# план Q4. Q4.5 (алерты, технические события) ещё не сделан: примеры на него
# добавляет та задача, которая его публикует, сюда же, а не новым файлом.
#
# 16.09.2026, MOS-41: у GET /api/forecasts?from=&to= нашли границу — to
# сравнивался как timestamptz <= 'ГГГГ-ММ-ДД 00:00' и вырезал весь названный
# день, «сегодня с сегодня» на полной базе отвечало пустым списком. Починили
# (from/to — даты, обе границы включительны) и в GET /api/objects/{id}/readings
# сделали сразу правильно. Ниже — не только код ответа, а число строк на
# границе дня: код 200 тут не отличает «отфильтровало верно» от «отфильтровало
# всё подряд».
#
# Запуск (нужен живой процесс, например локально на туннеле к базе):
#   BASE_URL=http://127.0.0.1:8000 bash contracts/examples/api/examples.sh
#
# Против стенда сертификат самоподписанный (docs/server.md) — curl без -k
# рвёт соединение до запроса, и это код 000, а не ответ сервера. CURL_OPTS
# пробрасывается во все вызовы curl, по умолчанию пуст:
#   BASE_URL=https://135.106.216.101 CURL_OPTS=-k bash contracts/examples/api/examples.sh

set -uo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
CURL_OPTS="${CURL_OPTS:-}"
# bash не даёт кириллицу в именах переменных (POSIX: только [a-zA-Z0-9_]) —
# идентификаторы латиницей, текст в выводе по-прежнему по-русски.
mismatch=0

check() {
    label="$1"; expected="$2"; shift 2
    actual=$(curl -s $CURL_OPTS -o /dev/null -w '%{http_code}' "$@")
    if [ "$actual" = "$expected" ]; then
        echo "OK          $label -> $actual"
    else
        echo "РАСХОЖДЕНИЕ $label -> ждали $expected, получили $actual"
        mismatch=1
    fi
}

# check, но требует подстроку в теле, а не только код ответа. Завела это
# 16.09.2026: на боевом стенде deploy/nginx/nginx.conf отдаёт SPA-заглушку
# (try_files ... /index.html) на ЛЮБОЙ путь без /api/ впереди, включая
# несуществующий, и код ответа у неё тоже 200 — check() на /health, /docs
# и /openapi.json был бы зелёным, даже если запрос до API вообще не доехал.
check_contains() {
    label="$1"; needle="$2"; shift 2
    body=$(curl -s $CURL_OPTS "$@")
    if echo "$body" | grep -qF "$needle"; then
        echo "OK          $label -> содержит ${needle}"
    else
        echo "РАСХОЖДЕНИЕ $label -> тело не содержит ${needle}, начало ответа: $(echo "$body" | head -c 120)"
        mismatch=1
    fi
}

# check, но сравнивает число элементов JSON-массива в теле, а не только код ответа —
# 200 с пустым списком и 200 с данными неотличимы по коду. expected_count — точное
# число или ">0" (для дат, чьё число строк растёт со временем, как today у прогнозов).
check_count() {
    label="$1"; expected_count="$2"; shift 2
    body=$(curl -s $CURL_OPTS "$@")
    actual_count=$(echo "$body" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "-1")
    if [ "$expected_count" = ">0" ]; then
        ok=$([ "$actual_count" -gt 0 ] 2>/dev/null && echo yes || echo no)
    else
        ok=$([ "$actual_count" = "$expected_count" ] && echo yes || echo no)
    fi
    if [ "$ok" = "yes" ]; then
        echo "OK          $label -> $actual_count строк"
    else
        echo "РАСХОЖДЕНИЕ $label -> ждали $expected_count строк, получили $actual_count"
        mismatch=1
    fi
}

# Проверка живости — единственный метод без роли и без записи в audit.user_action.
# По телу, не только по коду: SPA-заглушка на стенде тоже отвечает 200 (см. выше).
check_contains "GET /health" '"status":"ok"'                "$BASE_URL/health"

# Описание API генерирует FastAPI сам, наша работа — только сверить (М-17).
check_contains "GET /docs" "swagger-ui"                      "$BASE_URL/docs"
check_contains "GET /openapi.json" '"openapi"'                "$BASE_URL/openapi.json"

# Без заголовка — 401, а не 403: личность не опознана вовсе, а не в правах отказано.
check "GET /api/risks без входа"                            401 "$BASE_URL/api/risks"

# Роль диспетчера видит риски и прогнозы, но не журнал аудита (НФ-44).
check "GET /api/risks (dispatcher1)"                        200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/risks"
check "GET /api/forecasts (dispatcher1)"                    200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts"
# Расчёт (Q3) ещё не писал pred.forecast — 200 и пустой список, а не 404 и не 500.
check "GET /api/forecasts/1 (dispatcher1, id не найден)"    404 -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts/1"

# Регрессия на границу дня (MOS-41): from=to=сегодня обязан вернуть данные,
# если сегодня были прогоны, а не пустой список из-за строгого «<= полночь».
TODAY=$(date +%Y-%m-%d)
check_count "GET /api/forecasts?from=to=$TODAY (dispatcher1)" ">0" \
    -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts?from=$TODAY&to=$TODAY"
check_count "GET /api/forecasts?from=to=2020-01-01 (день до старта проекта)" 0 \
    -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts?from=2020-01-01&to=2020-01-01"

# Карточка объекта — паспорт, каналы, текущий риск, последние прогнозы (М-08).
check "GET /api/objects/1 (dispatcher1)"                    200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/1"
# last_reading_at — из smvu.reading напрямую, не из feat.section_daily (окно
# свёртки 2025-07-01…2026-06-30 обрезало бы семь участков со старыми показаниями).
check_contains "GET /api/objects/1 содержит last_reading_at" '"last_reading_at"' \
    -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/1"
check "GET /api/objects/999999999 (участка нет)"            404 -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/999999999"

# Ряд показаний — from/to обязательны (иначе смахнём 109 партиций smvu.reading).
check "GET /api/objects/1/readings без from/to"             422 -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/1/readings"
# 2025-10-13 — проверенный день с показаниями участка 1 (ровно 34 строки на
# 16.09.2026, история статична и не растёт, в отличие от прогнозов выше).
check_count "GET /api/objects/1/readings?from=to=2025-10-13" 34 \
    -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/1/readings?from=2025-10-13&to=2025-10-13"
check_count "GET /api/objects/1/readings?from=to=2025-10-12 (соседний день)" 0 \
    -H "X-User-Login: dispatcher1" "$BASE_URL/api/objects/1/readings?from=2025-10-12&to=2025-10-12"

# Q6 ещё не сделан — MOS-43 требует 200 и [] буквально, для списка и для карточки.
check "GET /api/orders (dispatcher1)"                       200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders"
check "GET /api/orders/42 (dispatcher1)"                    200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders/42"

# Журнал аудита — только администратору (НФ-43, НФ-44).
check "GET /api/audit (dispatcher1, должен отказать)"       403 -H "X-User-Login: dispatcher1" "$BASE_URL/api/audit"
check "GET /api/audit (admin1)"                             200 -H "X-User-Login: admin1"      "$BASE_URL/api/audit"

echo
echo "--- тело последнего успешного /api/audit (admin1), для примера формы ответа ---"
curl -s $CURL_OPTS -H "X-User-Login: admin1" "$BASE_URL/api/audit" | head -c 2000
echo

exit "$mismatch"
