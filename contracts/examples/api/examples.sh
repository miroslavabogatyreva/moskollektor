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

# Q6.5 (MOS-60) сделан — тело списка теперь объект {schema_version, total,
# items}, а не голый массив: check_count тут не годится, он умеет len() только
# на массиве верхнего уровня. Настоящий id берём из списка, а не выдумываем
# число — 64 заявки из двух прогонов не гарантируют, что id 42 существует.
check "GET /api/orders (dispatcher1)"                       200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders"
check "GET /api/orders/999999999 (заявки нет)"               404 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders/999999999"

# М-16 требует не «метод отвечает», а «метод отдаёт данные»: код 200 на пустом
# списке и код 200 на списке заявок ничем не отличаются. Считаем total из тела,
# не длину JSON верхнего уровня — он теперь объект на четыре поля, len() дал бы
# зелёный результат независимо от того, есть ли хоть одна заявка.
check_orders_total() {
    label="GET /api/orders (dispatcher1), М-16 — заявки есть"
    body=$(curl -s $CURL_OPTS -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders")
    total=$(echo "$body" | python3 -c "import json,sys; print(json.load(sys.stdin).get('total', -1))" 2>/dev/null || echo -1)
    if [ "$total" -gt 0 ] 2>/dev/null; then
        echo "OK          $label -> $total заявок"
    else
        echo "РАСХОЖДЕНИЕ $label -> ждали total > 0, получили $total"
        mismatch=1
    fi
}
check_orders_total

# Настоящая заявка по id из списка — 200 и то же id внутри тела.
check_order_detail() {
    label="GET /api/orders/{id} (dispatcher1), карточка по первой заявке списка"
    order_id=$(curl -s $CURL_OPTS -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); items=d.get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null)
    if [ -z "$order_id" ]; then
        echo "РАСХОЖДЕНИЕ $label -> в списке нет ни одной заявки, карточку проверять нечем"
        mismatch=1; return
    fi
    check "GET /api/orders/$order_id (dispatcher1)" 200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders/$order_id"
}
check_order_detail

# М-12: из заявки открывается прогноз, который её породил, и обратно. Проверка
# связывает два числа — id заявки, с которого начали, и id внутри order_ids
# прогноза, — а не сторожит одно поле. Одного forecast_id мало: он может указывать
# на прогноз, который про эту заявку не знает, и на экране переход «обратно»
# приведёт в пустоту, хотя ссылка «туда» работает.
check_order_forecast_link() {
    label="М-12 круговая сверка заявка -> прогноз -> заявка"
    hdr="X-User-Login: dispatcher1"
    order_id=$(curl -s $CURL_OPTS -H "$hdr" "$BASE_URL/api/orders" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); items=d.get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null)
    if [ -z "$order_id" ]; then
        echo "РАСХОЖДЕНИЕ $label -> GET /api/orders не отдал ни одной заявки в items, сверять нечего"
        mismatch=1; return
    fi
    # forecast_id теперь вложен в объект forecast карточки заявки (order.json), а не лежит плоским полем.
    forecast_id=$(curl -s $CURL_OPTS -H "$hdr" "$BASE_URL/api/orders/$order_id" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('forecast',{}).get('forecast_id','') if isinstance(d,dict) else '')" 2>/dev/null)
    if [ -z "$forecast_id" ]; then
        echo "РАСХОЖДЕНИЕ $label -> в теле заявки $order_id нет forecast.forecast_id, прогноз из заявки не открыть"
        mismatch=1; return
    fi
    back=$(curl -s $CURL_OPTS -H "$hdr" "$BASE_URL/api/forecasts/$forecast_id" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(' '.join(str(x) for x in d.get('order_ids',[])) if isinstance(d,dict) else '')" 2>/dev/null)
    if echo " $back " | grep -qF " $order_id "; then
        echo "OK          $label -> заявка $order_id -> прогноз $forecast_id -> order_ids [$back]"
    else
        echo "РАСХОЖДЕНИЕ $label -> заявка $order_id ссылается на прогноз $forecast_id, а его order_ids [$back] эту заявку не содержат"
        mismatch=1
    fi
}
check_order_forecast_link

# Журнал аудита — только администратору (НФ-43, НФ-44).
check "GET /api/audit (dispatcher1, должен отказать)"       403 -H "X-User-Login: dispatcher1" "$BASE_URL/api/audit"
check "GET /api/audit (admin1)"                             200 -H "X-User-Login: admin1"      "$BASE_URL/api/audit"

# Пороги и горизонт (MOS-110, Q4.12) — НФ-44 держит их на том же уровне, что
# журнал аудита: диспетчеру оба метода отвечают 403, а не только PUT.
check "GET /api/settings (dispatcher1, должен отказать)"    403 -H "X-User-Login: dispatcher1" "$BASE_URL/api/settings"
check "GET /api/settings (admin1)"                          200 -H "X-User-Login: admin1"      "$BASE_URL/api/settings"
# Отказ у диспетчера падает на require(), тело запроса не читается — в базу
# ничего не уходит, откатывать здесь нечего.
check "PUT /api/settings/forecast_horizon_h (dispatcher1)"  403 -X PUT -H "X-User-Login: dispatcher1" \
    -H "Content-Type: application/json" -d '{"value": 30}' "$BASE_URL/api/settings/forecast_horizon_h"

# Граница значения (нашли 57 и 58 на живом стенде: 0 и −5 отвечали 200) —
# постановка требует горизонт прогноза не меньше 24 часов, и это условие
# обязан проверять сам API, а не только глазами администратора.
check "PUT /api/settings/forecast_horizon_h (admin1, горизонт 0 — меньше 24)" \
    422 -X PUT -H "X-User-Login: admin1" \
    -H "Content-Type: application/json" -d '{"value": 0}' "$BASE_URL/api/settings/forecast_horizon_h"
check "PUT /api/settings/precision_min (admin1, 1.5 — вне (0,1))" \
    422 -X PUT -H "X-User-Login: admin1" \
    -H "Content-Type: application/json" -d '{"value": 1.5}' "$BASE_URL/api/settings/precision_min"

# Проверка по улову, не по счётчику (нашла 58): audit.user_action пишет КАЖДЫЙ
# запрос, включая GET-запросы этого же прогона, — разница длины «до/после»
# не изолирует именно наш PUT. Печатаем оба числа для картины, но ассерт ищет
# конкретную строку: метод, путь, код 200 и details.old/new словами «24»/«30».
#
# action_id, а не просто «есть подходящая строка в списке» (нашла я сама,
# 17.09.2026): при повторных прогонах в журнале остаётся строка от ПРОШЛОГО
# успешного PUT с тем же old=24/new=30, и поиск без границы находит её даже
# когда текущий прогон ничего не пишет — проверка была зелёной на сломанном
# коде. Граница — максимальный action_id ДО этого PUT, ищем строго после него.
check_settings_audit_trail() {
    label="PUT /api/settings/forecast_horizon_h (admin1) со следом old/new в audit"
    before_body=$(curl -s $CURL_OPTS -H "X-User-Login: admin1" "$BASE_URL/api/audit")
    before=$(echo "$before_body" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo -1)
    before_max_id=$(echo "$before_body" | python3 -c "
import json, sys
rows = json.load(sys.stdin)
print(max((r['action_id'] for r in rows), default=0))
" 2>/dev/null || echo 0)

    curl -s $CURL_OPTS -o /dev/null -X PUT -H "X-User-Login: admin1" \
        -H "Content-Type: application/json" -d '{"value": 30}' \
        "$BASE_URL/api/settings/forecast_horizon_h"
    audit_body=$(curl -s $CURL_OPTS -H "X-User-Login: admin1" "$BASE_URL/api/audit")
    after=$(echo "$audit_body" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo -1)

    # Вернуть горизонт как было — до печати результата, чтобы откат случился
    # даже если сама проверка ниже упадёт.
    curl -s $CURL_OPTS -o /dev/null -X PUT -H "X-User-Login: admin1" \
        -H "Content-Type: application/json" -d '{"value": 24}' \
        "$BASE_URL/api/settings/forecast_horizon_h"

    found=$(echo "$audit_body" | python3 -c "
import json, sys
rows = json.load(sys.stdin)
border = $before_max_id
for r in rows:
    if r.get('action_id', 0) <= border:
        continue
    d = r.get('details') or {}
    if (r.get('method') == 'PUT' and r.get('path') == '/api/settings/forecast_horizon_h'
            and r.get('status_code') == 200 and d.get('old') == '24' and d.get('new') == '30'):
        print('да')
        break
else:
    print('нет')
" 2>/dev/null)

    echo "строк в audit.user_action: до $before, после $after"
    if [ "$found" = "да" ]; then
        echo "OK          $label"
    else
        echo "РАСХОЖДЕНИЕ $label -> среди строк новее action_id $before_max_id нет PUT .../forecast_horizon_h, код 200, old=24/new=30"
        mismatch=1
    fi
}
check_settings_audit_trail

echo
echo "--- тело последнего успешного /api/audit (admin1), для примера формы ответа ---"
curl -s $CURL_OPTS -H "X-User-Login: admin1" "$BASE_URL/api/audit" | head -c 2000
echo

exit "$mismatch"
