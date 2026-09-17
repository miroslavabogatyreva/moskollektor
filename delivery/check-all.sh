#!/usr/bin/env bash
# check-all.sh — один прогон всех проверок проекта с привязкой к строкам приёмки.
#
# Зачем отдельный скрипт. Проверки у нас есть с первого дня, но лежат по углам:
# двенадцать самопроверок внутри модулей, contracts/examples/api/examples.sh,
# deploy/check-tls.sh, code/check_schema.py. Обойти их руками — десять команд,
# и после третьей забываешь, какая что доказывает. Здесь они собраны в один
# список, и рядом с каждой написано, КАКУЮ СТРОКУ ПРИЁМКИ она закрывает:
# проверка, не привязанная к строке, на приёмке не считается.
#
# Запуск:
#   bash delivery/check-all.sh                  # только то, что не требует сети
#   BASE_URL=https://135.106.216.101 CURL_OPTS=-k bash delivery/check-all.sh
#
# Код возврата 1, если упала хоть одна проверка. В bash здесь только латиница
# в именах переменных: кириллица валит скрипт целиком (docs/server.md).

set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

ok=0
fail=0
skip=0
failed_list=""

# run <строки приёмки> <название> <команда...>
run() {
  rows="$1"; name="$2"; shift 2
  out=$("$@" 2>&1)
  code=$?
  if [ $code -eq 0 ]; then
    printf 'OK    %-14s %s\n' "$rows" "$name"
    ok=$((ok + 1))
  else
    printf 'УПАЛА %-14s %s\n' "$rows" "$name"
    printf '%s\n' "$out" | tail -5 | sed 's/^/        /'
    fail=$((fail + 1))
    failed_list="$failed_list $name"
  fi
}

skip_msg() {
  printf 'ПРОПУСК %-12s %s\n' "$1" "$2"
  skip=$((skip + 1))
}

# Одно число участков во всех документах (НФ-16, задача MOS-88). Расчётное
# допущение 825 км / 200 м = 4125 отброшено 17.09.2026: пикет оказался
# десятиметровым, участков 3173. Проверка ищет старое число там, где мы говорим
# от своего имени, и разрешает его там, где строка сама называет его отменённым.
# Протоколы встреч не трогаем никогда: там записано, что заказчик сказал вслух.
# Исключения по НОМЕРУ строки здесь нарочно нет — номер уезжает при первой же
# правке файла, и проверка начнёт пропускать не то, что от неё ждут.
# Номера задачи в белом списке тоже нет, и по той же причине: «MOS-88» пишет
# и верная строка, и неверная. Строка «objects_total=4125, задача MOS-88» с ним
# проходила бы молча — нашла эту дыру вторая проверяющая. В список попадают
# только слова, которыми строка называет число ОТМЕНЁННЫМ: «отброшено», «снято»,
# «по старому допущению». Список закрытый и растить его не надо: пишите в новых
# строках «отброшено», иначе через месяц здесь будет десяток синонимов, и любой
# из них начнёт пропускать живое 4125.
odno_chislo_uchastkov() {
  # -w обязателен: без него «4125» находится внутри «14125» и «41256», а такие
  # числа лежат в потрохах PDF пачками. Каталог books тоже исключён — книги
  # заказчика не наш текст, и grep по ним даёт сотни строк из служебных таблиц.
  hits=$(grep -rnIw "4125\|4 125" docs/ README.md CLAUDE.md \
           --exclude-dir=meetings --exclude-dir=books 2>/dev/null \
         | grep -v "отброш\|снят\|старому допущению\|старого допущения")
  [ -z "$hits" ] && return 0
  # Обрезаю строку: одна ячейка docs/acceptance-test.md занимает три экрана,
  # и без обрезки падение печатает её целиком вместо списка виноватых файлов.
  printf 'старое число участков без пометки об отмене:\n%s\n' "$(printf '%s' "$hits" | cut -c1-160)"
  return 1
}

echo "=== документы ==="
run "НФ-16"        "одно число участков"     odno_chislo_uchastkov

echo
echo "=== схема и миграции ==="
run "—"            "порядок миграций"        python3 code/check_schema.py

echo
echo "=== самопроверки модулей ==="
run "Ф-73"         "объяснение риска"        env PYTHONPATH=backend "$PY" -m app.domain.explain
run "НФ-43"        "роли и доступ"           env PYTHONPATH=backend "$PY" -m app.auth.deps
run "—"            "запись прогноза"         env PYTHONPATH=backend "$PY" -m app.worker.publish
run "—"            "клиент модели"           env PYTHONPATH=backend "$PY" -m app.mlclient.client
run "—"            "выбор факторов"          env PYTHONPATH=backend "$PY" -m app.worker.run --selfcheck
# Методику порогов считают ДВА модуля: verdict() в predictive_metrics.py и
# строки_качества() в check_metrics.py. Формула в них одна и та же, написана
# дважды, и разойтись они могут молча. Обе самопроверки базы не требуют, поэтому
# стоят здесь, а не в блоке, который ждёт DATABASE_URL: строгое «больше 0,7»
# должно проверяться в каждом прогоне, а не только на стенде.
run "М-10, М-13"   "правило заявки"          env PYTHONPATH=backend "$PY" -m app.domain.order_rules
run "М-18, М-19"   "строгость порогов"       python3 code/predictive_metrics.py
run "М-18, М-20"   "строки качества"         python3 code/check_metrics.py --selfcheck
if [ -n "${DATABASE_URL:-}" ]; then
  run "М-18, М-21"  "методика метрик"        python3 code/check_metrics.py
else
  skip_msg "М-18, М-21" "методика метрик — задайте DATABASE_URL"
fi

# М-12 здесь помечена половиной строки нарочно: check_orders.py спрашивает базу
# и видит внешний ключ, а строка приёмки требует ещё и перехода заявка → прогноз
# → заявка через API и на экране. Пока Q6.5 не сдана, GET /api/orders отдаёт
# пустой список, и зелёная строка отсюда означает «связь лежит в данных».
if [ -n "${DATABASE_URL:-}" ]; then
  run "М-09…М-11, М-13, М-12 наполовину" "заявки на стенде" python3 code/check_orders.py
else
  skip_msg "М-09…М-13" "заявки на стенде — задайте DATABASE_URL"
fi
run "—"            "шаблоны объяснений"      python3 code/check_explain_templates.py

echo
echo "=== стенд ==="
if [ -n "${BASE_URL:-}" ]; then
  run "М-14, М-17"  "примеры вызовов API"    env BASE_URL="$BASE_URL" CURL_OPTS="${CURL_OPTS:-}" bash contracts/examples/api/examples.sh
else
  skip_msg "М-14, М-17" "примеры вызовов API — задайте BASE_URL"
fi

if [ -n "${TLS_HOST:-}" ]; then
  run "НФ-75"       "версии TLS и шифры"     sh deploy/check-tls.sh "$TLS_HOST"
else
  skip_msg "НФ-75" "версии TLS — задайте TLS_HOST"
fi

echo
echo "итого: успешно $ok, упало $fail, пропущено $skip"
if [ $fail -gt 0 ]; then
  echo "упали:$failed_list"
  exit 1
fi
