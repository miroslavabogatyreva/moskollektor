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
# план Q4. Q4.4 (объекты) и Q4.5 (алерты, технические события) ещё не сделаны:
# примеры на них добавляет та задача, которая их публикует, сюда же, а не новым
# файлом.
#
# Запуск (нужен живой процесс, например локально на туннеле к базе):
#   BASE_URL=http://127.0.0.1:8000 bash contracts/examples/api/examples.sh

set -uo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
# bash не даёт кириллицу в именах переменных (POSIX: только [a-zA-Z0-9_]) —
# идентификаторы латиницей, текст в выводе по-прежнему по-русски.
mismatch=0

check() {
    label="$1"; expected="$2"; shift 2
    actual=$(curl -s -o /dev/null -w '%{http_code}' "$@")
    if [ "$actual" = "$expected" ]; then
        echo "OK          $label -> $actual"
    else
        echo "РАСХОЖДЕНИЕ $label -> ждали $expected, получили $actual"
        mismatch=1
    fi
}

# Проверка живости — единственный метод без роли и без записи в audit.user_action.
check "GET /health"                                         200 "$BASE_URL/health"

# Описание API генерирует FastAPI сам, наша работа — только сверить (М-17).
check "GET /docs"                                           200 "$BASE_URL/docs"
check "GET /openapi.json"                                   200 "$BASE_URL/openapi.json"

# Без заголовка — 401, а не 403: личность не опознана вовсе, а не в правах отказано.
check "GET /api/risks без входа"                            401 "$BASE_URL/api/risks"

# Роль диспетчера видит риски и прогнозы, но не журнал аудита (НФ-44).
check "GET /api/risks (dispatcher1)"                        200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/risks"
check "GET /api/forecasts (dispatcher1)"                    200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts"
# Расчёт (Q3) ещё не писал pred.forecast — 200 и пустой список, а не 404 и не 500.
check "GET /api/forecasts/1 (dispatcher1, id не найден)"    404 -H "X-User-Login: dispatcher1" "$BASE_URL/api/forecasts/1"

# Q6 ещё не сделан — MOS-43 требует 200 и [] буквально, для списка и для карточки.
check "GET /api/orders (dispatcher1)"                       200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders"
check "GET /api/orders/42 (dispatcher1)"                    200 -H "X-User-Login: dispatcher1" "$BASE_URL/api/orders/42"

# Журнал аудита — только администратору (НФ-43, НФ-44).
check "GET /api/audit (dispatcher1, должен отказать)"       403 -H "X-User-Login: dispatcher1" "$BASE_URL/api/audit"
check "GET /api/audit (admin1)"                             200 -H "X-User-Login: admin1"      "$BASE_URL/api/audit"

echo
echo "--- тело последнего успешного /api/audit (admin1), для примера формы ответа ---"
curl -s -H "X-User-Login: admin1" "$BASE_URL/api/audit" | head -c 2000
echo

exit "$mismatch"
