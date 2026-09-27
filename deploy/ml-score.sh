#!/bin/sh
# Расчёт модели v3 на край данных: кладёт score.json в том, откуда его читает worker.
# Задача 3.20 (MOS-166). Запускается по расписанию на хосте, раз в час.
#
# ПОЧЕМУ НЕ ИЗ WORKER. Чтобы worker запустил соседний контейнер, ему нужен докер-сокет,
# а сокет — это права root на всю машину. Нам нужен один JSON, и ради него отдавать
# машину незачем. Поэтому запуск снаружи, а обмен — через том.
#
# ПОЧЕМУ ВО ВРЕМЕННЫЙ ФАЙЛ, А ПОТОМ mv. Расчёт идёт две минуты и пишет постепенно.
# Worker в это время может прийти за файлом и прочитать половину — контракт он
# проверит, но половина валидного JSON это не половина прогноза, это отказ.
# Переименование внутри одной файловой системы атомарно: worker видит либо
# вчерашний файл целиком, либо сегодняшний целиком.
#
# Имена переменных латиницей нарочно: /bin/sh на Ubuntu это dash,
# и кириллическое имя переменной он не разбирает вовсе — «ОБРАЗ=...: not found».
# Поймано первым же запуском на стенде 22.09.2026.
#
# Проверка после установки — docs/server.md, раздел про расчёт модели.
set -eu

IMAGE="${ML_SCORE_IMAGE:-moskollektor/ml-score:lgbm-v3-bag-2026.09.21b}"
DATA="${ML_SCORE_DATA:-/home/nikolay-hakaton/lct-task8-delivery_20260921/score-data/data}"
VOLUME="${ML_SCORE_VOLUME:-moskollektor_score}"
LOG="${ML_SCORE_LOG:-/var/log/ml-score.log}"

# Срез — край выгрузки, а не текущий момент. Выгрузка заказчика кончается
# 30.06.2026, и расчёт на сегодня дал бы пустые признаки у всех коллекторов:
# та же ловушка, что описана у worker в deploy/docker-compose.yml.
EDGE="2026-06-30 23:59:59"
AS_OF="${ML_SCORE_AS_OF:-$EDGE}"

# ПРОИГРЫВАНИЕ АРХИВА. Данные стоят на краю, и без этого на стенде не появляется
# ни одного нового предупреждения и ни одной новой заявки. Задан ML_SCORE_REPLAY_FROM —
# срез идёт от него вперёд: архивный момент = FROM + (сейчас − START) × SPEED,
# с точностью до часа, не дальше края. SPEED 24 — сутки архива за час; при запуске
# раз в час каждый расчёт сдвигает срез на сутки, июнь проходит за 30 часов.
# Worker берёт срез из файла сам (app.worker.scheduler.срез_проигрывания).
# Время архива московское без пояса, часы хоста — в UTC, поэтому TZ у date явный.
if [ -n "${ML_SCORE_REPLAY_FROM:-}" ] && [ -z "${ML_SCORE_AS_OF:-}" ]; then
    replay_start=$(date -d "${ML_SCORE_REPLAY_START:?задайте ML_SCORE_REPLAY_START}" +%s)
    replay_from=$(TZ=Europe/Moscow date -d "$ML_SCORE_REPLAY_FROM" +%s)
    replay_edge=$(TZ=Europe/Moscow date -d "$EDGE" +%s)
    replay_t=$(( replay_from + ( $(date +%s) - replay_start ) * ${ML_SCORE_REPLAY_SPEED:-24} / 3600 * 3600 ))
    [ "$replay_t" -lt "$replay_from" ] && replay_t=$replay_from
    [ "$replay_t" -gt "$replay_edge" ] && replay_t=$replay_edge
    AS_OF=$(TZ=Europe/Moscow date -d "@$replay_t" '+%Y-%m-%d %H:%M:%S')
fi

started=$(date +%s)
echo "$(date -Iseconds) старт, срез $AS_OF, образ $IMAGE" >> "$LOG"

docker run --rm --cpus "${ML_SCORE_CPUS:-4}" \
    -v "$DATA":/app/data:ro \
    -v "$VOLUME":/out \
    "$IMAGE" --as-of "$AS_OF" --out /out/score.json.tmp >> "$LOG" 2>&1

# Файл готов целиком — только теперь подставляем его под worker.
docker run --rm -v "$VOLUME":/out --entrypoint sh "$IMAGE" \
    -c 'test -s /out/score.json.tmp && mv /out/score.json.tmp /out/score.json'

elapsed=$(( $(date +%s) - started ))
echo "$(date -Iseconds) готово за ${elapsed} с" >> "$LOG"

# Норматив М-21 — 300 секунд на весь расчёт. Замер 22.09.2026: 120 с с --cpus 4.
# Если однажды перестанет укладываться, это надо увидеть в журнале, а не на приёмке.
if [ "$elapsed" -gt "${ML_SCORE_WARN_S:-240}" ]; then
    echo "$(date -Iseconds) ВНИМАНИЕ: расчёт занял ${elapsed} с при нормативе М-21 300 с" >> "$LOG"
fi
