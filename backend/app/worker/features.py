"""Сборка вектора признаков по contracts/features.v1.yaml. Задача Q3.3 (MOS-33).

Двадцать два числа на участок, в порядке контракта. Порядок сам является контрактом:
переставленные колонки дают уверенный неверный ответ без единой ошибки.

**Это самая дорогая стадия расчёта** — 24,3 секунды из 32 на весь расчёт.
Оптимизируем чтение, а не модель: сам инференс занимает 1,2 мс.

УСТРОЙСТВО. Четыре запроса к базе вместо одного большого, и свёртка в Python:

  1. feat.section_daily за 365 суток   → девять признаков по участку
  2. smvu.channel                      → сколько каналов числится за участком
  3. feat.channel_daily за 365 суток   → счётчики и фоны по каналу
  4. smvu.reading за 30 суток          → интервалы между записями по каналу

Замеры стадии целиком на всех 3 173 участках, стенд 135.106.216.101, срез
30.06.2026 23:59:59. Было 17.09.2026 до правки — 48,5 с, из них 25,7 с счётчики
по каналу и 22,4 с интервалы. Стало 24,3 с: счётчики читают суточную свёртку
feat.channel_daily (миграция 022) и стоят 4,2 с, интервалы обходятся одной
группировкой вместо двух и стоят 19,0 с. Вектор признаков при этом не изменился
ни в одной из 69 806 клеток — 3 173 участка × 22 признака, сверка попарная.

Почему свёртка в Python, а не в SQL. Девять признаков считаются ПО КАНАЛУ
(docs/HLD.md разд. 6.3-трис: «все четыре считаются внутри канала, относительно
его собственного поведения»), а строка для модели одна на участок. Свести
10 720 каналов в 3 173 участка — это один проход по списку, в SQL он стоил бы
ещё одного уровня группировки поверх оконных функций.

**Как сворачиваем канал в участок: берём худший канал.** Участок с одним умирающим
каналом из четырёх должен выглядеть как участок с проблемой, а не как средний
по больнице: среднее размажет обрыв темпа у одного канала по трём здоровым.
Поэтому по «плохо, когда много» берём максимум (дребезг, доля «Неопределен»,
паузы), по «плохо, когда мало» — минимум (темп записей к своему фону).
**В contracts/features.v1.yaml этого правила нет** — там у признака стоит
scope: channel и не сказано, как он попадает в строку участка. Это дыра
в контракте, а не наше право решать: правка контракта — PR к Николаю.

ЧТО ЗНАЧИТ NULL. Правило контракта rules.null_means_missing: null — «признака нет»,
а не ноль. Ноль у days_since_last_reading означает «писал сегодня», null — «не писал
ни разу за 365 суток». Границы, на которых мы отдаём null, взяты из замеров
в самом контракте: у 104 каналов из 11 483 медианный интервал равен нулю
(silence_normalized и gap_to_median не считаются), доля интервалов длиннее p95
требует хотя бы 20 интервалов за неделю (набирают 640 каналов).

ЧЕГО МЫ НЕ СЧИТАЕМ И ПОЧЕМУ. Признаки fault_30d и neighbor_fault_7d читаются
из smvu.fault_episode. 16.09.2026 таблица была пуста; с тех пор её строит
backend/app/ingest/fault_episodes.py (25 440 эпизодов на 21.09.2026), и признаки
ожили. Пустая таблица не отличает «отказа не было» от «эпизоды не считались», поэтому
при нуле эпизодов оба признака едут null, а не false. Определение отказа здесь
экранное, а не D5 модели, — см. ponytail над ОТКАЗЫ_ПО_УЧАСТКУ (MOS-153).

ЗАПУСК САМОПРОВЕРКИ (нужна база, туннель на стенд):
    DATABASE_URL=postgresql://... .venv/bin/python backend/app/worker/features.py
"""

import asyncio
import math
import os
import time
from datetime import datetime, timedelta

from app.mlclient.client import FEATURE_NAMES

# ponytail: медиана и p95 интервалов канала считаются по окну в 30 суток, а не по всей
# истории. Перцентиль по 313 млн строк — это отдельная таблица с предрасчётом и своя
# миграция; 30 суток характеризуют собственный ритм канала и стоят одного прохода
# по 4,6 млн строк. Упрётся точность — заводить feat.channel_daily и считать по году.
ОКНО_ИНТЕРВАЛОВ_СУТОК = 30

# Меньше этого числа интервалов за неделю — доля длинных интервалов не считается.
# Число из contracts/features.v1.yaml: при недельном окне 20 интервалов набирают
# 640 каналов из 11 483, у остальных приедет null, и это правильное значение.
МИНИМУМ_ИНТЕРВАЛОВ = 20


# ---------------------------------------------------------------------------
# Запросы
# ---------------------------------------------------------------------------

# Девять признаков по участку одним сканом суточной свёртки. 3 173 × 365 = 1,16 млн
# строк вместо 55 млн строк журнала за тот же год — выигрыш в 47 раз.
ПО_УЧАСТКУ = """
SELECT section_id,
       sum(readings_total) FILTER (WHERE day >  $1::date - 1)   AS readings_1d,
       sum(readings_total) FILTER (WHERE day >  $1::date - 7)   AS readings_7d,
       sum(readings_total) FILTER (WHERE day >  $1::date - 30)  AS readings_30d,
       sum(readings_total)                                      AS readings_365d,
       sum(alarms_total)   FILTER (WHERE day >  $1::date - 7)   AS alarms_7d,
       sum(alarms_total)                                        AS alarms_365d,
       max(day) FILTER (WHERE readings_total > 0)               AS last_day
  FROM feat.section_daily
 WHERE day > $1::date - 365 AND day <= $1::date
   AND ($2::int[] IS NULL OR section_id = ANY($2))
 GROUP BY section_id
"""

# Последняя запись участка по журналу, без годового окна. Нужен только для
# участков, которых нет в свёртке вовсе: свёртка живёт год, а участок, замолчавший
# раньше, в неё не попадает — и «дней с последней записи» у него выходит null,
# «не знаем, когда писал». Для самых долго молчащих это худший из возможных
# ответов: именно им объяснение нужнее всех. Запрос идёт по индексу channel_id.
ПОСЛЕДНЯЯ_ЗАПИСЬ_БЕЗ_ОКНА = """
SELECT c.section_id, max(r.read_time)::date AS last_day
  FROM smvu.channel c
  JOIN smvu.reading r ON r.channel_id = c.channel_id
 WHERE c.section_id = ANY($1)
 GROUP BY c.section_id
"""

# Выше этого числа непокрытых участков дело не в молчании, а в недосчитанной
# свёртке. Тогда в журнал не идём: 12 627 каналов без окна — это скан всех
# 109 партиций smvu.reading, а он кладёт базу. На 30.06.2026 непокрытых семь.
ПРЕДЕЛ_ДОСЧЁТА = 200


# Сколько каналов числится за участком. Заглушки сюда не попадают: у канала,
# которого нет в справочнике заказчика, section_id остаётся NULL.
КАНАЛОВ_НА_УЧАСТКЕ = """
SELECT section_id, count(*) AS каналов
  FROM smvu.channel
 WHERE section_id IS NOT NULL
   AND ($1::int[] IS NULL OR section_id = ANY($1))
 GROUP BY section_id
"""

# Счётчики по каналу за неделю плюс два фона. Фон за 8 недель берётся ДО недельного
# окна, иначе неделя посчиталась бы сама против себя и отношение всегда было бы около 1.
#
# ЧИТАЕМ СУТОЧНУЮ СВЁРТКУ feat.channel_daily, А НЕ ЖУРНАЛ (задача Q3.8, MOS-99).
# Замер 17.09.2026 на стенде: этот запрос по журналу стоил 25,7 с из 48,5 с всей
# стадии и читал 60 096 602 строки smvu.reading. Та же свёртка по суткам — 662 596
# строк, в девяносто один раз меньше.
#
# ПОЧЕМУ ЗАПРОС НЕ СВЁЛСЯ К ОДНОМУ СКАНУ СВЁРТКИ. Окна считаются от МОМЕНТА среза,
# а свёртка сложена по СУТКАМ, и границы окон почти никогда не приходятся на полночь.
# При срезе 30.06.2026 23:59:59 недельное окно начинается 23.06 в 23:59:59 — это
# значит, что сутки 23.06 попадают в окно не целиком, а последней своей секундой.
# Округлить такие сутки в любую сторону значит соврать: выбросить их целиком —
# потерять запись, взять целиком — прибавить чужие сутки.
#
# Поэтому запрос сложен из двух частей. Часть «сут» берёт из свёртки ЦЕЛЫЕ сутки,
# исключая пять граничных дат. Часть «край» читает журнал ровно за эти пять суток
# и применяет к ним ТЕ ЖЕ условия по времени, что стояли в прежнем запросе, —
# текст условий переписан дословно, поэтому смысл окон не поменялся ни на секунду.
# Пять суток журнала — это около 825 тысяч строк вместо шестидесяти миллионов.
#
# Граничных дат ровно пять: начало недельного окна, начало месячного, начало окна
# восьми недель, начало годового и сами сутки среза. Последние нужны потому, что
# при боевом расчёте срез приходится на середину дня, и без них в счётчики попали
# бы записи, сделанные ПОСЛЕ момента расчёта.
ПО_КАНАЛУ = """
WITH границы AS (
    SELECT ARRAY[($1::timestamptz - interval '365 days')::date,
                 ($1::timestamptz - interval '63 days')::date,
                 ($1::timestamptz - interval '30 days')::date,
                 ($1::timestamptz - interval '7 days')::date,
                 $1::date] AS дни
), сут AS (
    SELECT d.channel_id,
           sum(d.readings_total)  FILTER (WHERE d.day > ($1::timestamptz - interval '7 days')::date)   AS n7,
           sum(d.readings_total)  FILTER (WHERE d.day > ($1::timestamptz - interval '30 days')::date)  AS n30,
           sum(d.fault_total)     FILTER (WHERE d.day > ($1::timestamptz - interval '7 days')::date)   AS chatter7,
           sum(d.undefined_total) FILTER (WHERE d.day > ($1::timestamptz - interval '7 days')::date)   AS undefined7,
           sum(d.readings_total)  FILTER (WHERE d.day <= ($1::timestamptz - interval '7 days')::date
                                            AND d.day >  ($1::timestamptz - interval '63 days')::date) AS n8w,
           sum(d.readings_total)                                                                       AS n365,
           max(d.last_read)                                                                            AS last_read
      FROM feat.channel_daily d, границы г
     WHERE d.day > ($1::timestamptz - interval '365 days')::date
       AND d.day <= $1::date
       AND NOT (d.day = ANY(г.дни))
     GROUP BY d.channel_id
), край AS (
    SELECT r.channel_id,
           count(*) FILTER (WHERE r.read_time > $1::timestamptz - interval '7 days')                    AS n7,
           count(*) FILTER (WHERE r.read_time > $1::timestamptz - interval '30 days')                   AS n30,
           count(*) FILTER (WHERE r.read_time > $1::timestamptz - interval '7 days'
                              AND r.value_text = 'Неисправен')                             AS chatter7,
           count(*) FILTER (WHERE r.read_time > $1::timestamptz - interval '7 days'
                              AND r.value_text = 'Неопределен')                            AS undefined7,
           count(*) FILTER (WHERE r.read_time <= $1::timestamptz - interval '7 days'
                              AND r.read_time >  $1::timestamptz - interval '63 days')                  AS n8w,
           count(*)                                                                        AS n365,
           max(r.read_time)                                                                AS last_read
      FROM границы г
      CROSS JOIN LATERAL unnest(г.дни) AS g(день)
      JOIN smvu.reading r ON r.read_time >= g.день::timestamptz
                         AND r.read_time <  (g.день + 1)::timestamptz
     WHERE r.read_time > $1::timestamptz - interval '365 days'
       AND r.read_time <= $1::timestamptz
     GROUP BY r.channel_id
)
SELECT c.channel_id,
       c.section_id,
       c.collector,
       coalesce(s.n7, 0)         + coalesce(k.n7, 0)         AS n7,
       coalesce(s.n30, 0)        + coalesce(k.n30, 0)        AS n30,
       coalesce(s.chatter7, 0)   + coalesce(k.chatter7, 0)   AS chatter7,
       coalesce(s.undefined7, 0) + coalesce(k.undefined7, 0) AS undefined7,
       coalesce(s.n8w, 0)        + coalesce(k.n8w, 0)        AS n8w,
       coalesce(s.n365, 0)       + coalesce(k.n365, 0)       AS n365,
       greatest(s.last_read, k.last_read)                    AS last_read
  FROM smvu.channel c
  LEFT JOIN сут  s ON s.channel_id = c.channel_id
  LEFT JOIN край k ON k.channel_id = c.channel_id
 WHERE c.section_id IS NOT NULL
   AND ($2::int[] IS NULL OR c.section_id = ANY($2))
   -- Прежний запрос соединял канал с журналом внутренним JOIN, то есть канал
   -- без единой записи за год в выдачу не попадал вовсе. Условие повторяет это:
   -- канал, которого нет ни в свёртке, ни на краю, не писал за год ни разу.
   AND (s.channel_id IS NOT NULL OR k.channel_id IS NOT NULL)
"""

# Интервалы между записями канала: медиана, p95, доля длинных, залипание значения.
# Сортировка по (read_time, journal_id), а не по одному времени: за 2025 год
# 1 305 295 строк делят канал и метку времени, и без тай-брейка соседние записи
# встают в произвольном порядке — число эпизодов «Неисправен» меняется на 7,2 %
# (contracts/features.v1.yaml, data_caveats).
#
# Всё одним запросом, хотя порог p95 известен только после первой группировки.
# Разными запросами это стоило 14,9 с + 15,4 с на полном парке (замер 16.09.2026):
# оба читали одно и то же окно в 30 суток, то есть 4,6 млн строк журнала дважды.
#
# ОДНА ГРУППИРОВКА ВМЕСТО ДВУХ (задача Q3.5, 17.09.2026). Раньше запрос группировал
# 4,6 млн строк по каналу, а потом соединял результат ОБРАТНО с тем же CTE и
# группировал второй раз — только затем, чтобы сосчитать интервалы длиннее p95.
# Второй проход стоил половины запроса. Теперь недельные интервалы канала едут
# из первой же группировки массивом, и длинные считаются по этому массиву:
# массив короткий, в нём интервалы канала за неделю, а не за месяц и не по парку.
#
# AS MATERIALIZED оставлено: без него планировщик вправе подставить тело CTE
# в оба места и вернуть тот же двойной проход по журналу.
#
# ponytail: 19 секунд этого запроса — потолок без нового индекса. Уходят они
# не на сортировку, а на сам проход: 4 649 941 строка журнала за 30 суток, три
# оконные функции поверх. Это проверено, а не предположено — 17.09.2026 я поднял
# work_mem с 64 МБ до 512 МБ, сортировка перестала уходить на диск
# («external merge Disk: 183 888 kB» сменилось на «quicksort Memory: 395 762 kB»),
# а время не изменилось вовсе: 15,81 с против 15,77 с. Настройки сервера трогать
# не нужно. Убрать проход можно только индексом
# (channel_id, read_time, journal_id) на smvu.reading — третья колонка нужна
# из-за тай-брейка, — но это индекс по 313 млн строк, и строить его ради
# 19 секунд при нормативе 300 незачем. Упрёмся в норматив на железе заказчика —
# вот тогда.
ИНТЕРВАЛЫ = """
WITH подряд AS MATERIALIZED (
    SELECT r.channel_id,
           r.read_time,
           r.value_text,
           extract(epoch FROM r.read_time - lag(r.read_time)
                   OVER (PARTITION BY r.channel_id ORDER BY r.read_time, r.journal_id))::float8 AS дельта,
           lag(r.value_text)    OVER (PARTITION BY r.channel_id ORDER BY r.read_time, r.journal_id) AS пред1,
           lag(r.value_text, 2) OVER (PARTITION BY r.channel_id ORDER BY r.read_time, r.journal_id) AS пред2
      FROM smvu.reading r
      JOIN smvu.channel c ON c.channel_id = r.channel_id
     WHERE r.read_time > $1::timestamptz - ($3::int * interval '1 day') AND r.read_time <= $1::timestamptz
       AND c.section_id IS NOT NULL
       AND ($2::int[] IS NULL OR c.section_id = ANY($2))
), порог AS (
    SELECT channel_id,
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY дельта) AS медиана,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY дельта) AS p95,
           count(дельта)                                        AS интервалов,
           count(дельта) FILTER (WHERE read_time > $1::timestamptz - interval '7 days') AS интервалов_7d,
           max(дельта)   FILTER (WHERE read_time > $1::timestamptz - interval '7 days') AS макс_дельта_7d,
           bool_or(read_time > $1::timestamptz - interval '7 days'
                   AND value_text IS NOT NULL
                   AND value_text = пред1 AND value_text = пред2)          AS freeze3,
           array_agg(дельта) FILTER (WHERE read_time > $1::timestamptz - interval '7 days'
                                      AND дельта IS NOT NULL)              AS дельты_7d
      FROM подряд GROUP BY channel_id
)
SELECT п.channel_id, п.медиана, п.p95, п.интервалов, п.интервалов_7d,
       п.макс_дельта_7d, п.freeze3,
       coalesce(cardinality(п.дельты_7d), 0)                               AS всего_7d,
       (SELECT count(*) FROM unnest(п.дельты_7d) д WHERE д > п.p95)        AS длинных_7d
  FROM порог п
"""

# ponytail: экранное определение отказа (smvu.fault_episode, «Неисправен» + «Неопределен»)
# и ключ c.collector из 30 префиксов, а не D5 и не 16 коллекторов дерева (MOS-153).
# Жив только на пути заглушки: у v3 прогноз приходит готовым из score.json.
# Переводить на code/model_failure.py:ЭПИЗОДЫ_МОДЕЛИ, если путь заглушки вернётся в бой.
ОТКАЗЫ_ПО_УЧАСТКУ = """
SELECT section_id, count(*) AS отказов
  FROM smvu.fault_episode
 WHERE started_at > $1::timestamptz - interval '30 days' AND started_at <= $1::timestamptz
   AND section_id IS NOT NULL
   AND ($2::int[] IS NULL OR section_id = ANY($2))
 GROUP BY section_id
"""

# ponytail: экранное определение отказа (smvu.fault_episode, «Неисправен» + «Неопределен»)
# и ключ c.collector из 30 префиксов, а не D5 и не 16 коллекторов дерева (MOS-153).
# Жив только на пути заглушки: у v3 прогноз приходит готовым из score.json.
# Переводить на code/model_failure.py:ЭПИЗОДЫ_МОДЕЛИ, если путь заглушки вернётся в бой.
ОТКАЗЫ_ПО_КОЛЛЕКТОРУ = """
SELECT c.collector, count(*) AS отказов
  FROM smvu.fault_episode e
  JOIN smvu.channel c ON c.channel_id = e.channel_id
 WHERE e.started_at > $1::timestamptz - interval '7 days' AND e.started_at <= $1::timestamptz
   AND c.collector IS NOT NULL
   -- С 21.09.2026 коллектор стоит и у каналов без участка (охранные зоны, здания
   -- диспетчерских). Условие на участок держит признак таким, каким он был до этого:
   -- считаются только отказы каналов на участках коллектора. Считать ли зоны и
   -- здания соседями — решение по модели, а не по загрузчику.
   AND c.section_id IS NOT NULL
 GROUP BY c.collector
"""


# ---------------------------------------------------------------------------
# Свёртки
# ---------------------------------------------------------------------------

class Хронометр:
    """Сколько миллисекунд занял каждый запрос стадии.

    Один общий счёт «сборка признаков 48 секунд» не говорит, что чинить.
    Разбивка по запросам показывает это прямо: 16.09.2026 из 56,9 секунды
    26,1 приходилось на один запрос ПО_КАНАЛУ, и оптимизировать надо было его,
    а не стадию вообще.
    """

    def __init__(self):
        self.мс = {}

    async def запрос(self, имя, корутина):
        t = time.perf_counter()
        итог = await корутина
        self.мс[имя] = round((time.perf_counter() - t) * 1000)
        return итог


def _худший(значения, как="max"):
    """Худший канал участка. None среди значений не считается за ответ."""
    живые = [з for з in значения if з is not None]
    if not живые:
        return None
    return max(живые) if как == "max" else min(живые)


def _доля(часть, всего):
    """Доля с честным null: от нуля записей доля не определена, а не равна нулю."""
    return None if not всего else часть / всего


async def покрытие_свёртки(conn, as_of, section_ids=None):
    """Сколько суток из 365 покрыто суточной свёрткой.

    Проверка не украшение. feat.section_daily заполняет отдельная стадия расчёта,
    и если она не отработала, все девять признаков по участку выйдут НУЛЯМИ —
    без ошибки и без предупреждения. Ноль показаний за год и «свёртку не считали»
    для модели выглядят одинаково, а значат противоположное.
    """
    return await conn.fetchval(
        """SELECT count(DISTINCT day) FROM feat.section_daily
            WHERE day > $1::date - 365 AND day <= $1::date
              AND ($2::int[] IS NULL OR section_id = ANY($2))""",
        as_of, section_ids)


async def освежить_канальную_свёртку(conn, as_of):
    """Догнать feat.channel_daily до среза. Возвращает (суток, обновлено, догоняли_год).

    **Свежесть входа — забота самой стадии, а не того, кто её зовёт.** Суточную
    свёртку по участку догоняет отдельная стадия расчёта, и это уже однажды вышло
    боком: пока функция не заполняла silent_channels, признак silent_sensor_share
    выходил ровно нулём у всех участков без единой ошибки. Свёртку по каналу
    я догоняю здесь, в том же коде, который её читает, — тогда «забыли позвать»
    становится невозможным состоянием.

    **Проверка связывает два числа, а не сторожит одно.** Сколько суток года
    покрыто свёрткой по каналу и сколько — свёрткой по участку. Они обязаны
    сойтись: обе строятся из одного журнала за одно и то же окно. Один счётчик
    здесь обманул бы — «в таблице 662 596 строк» верно и тогда, когда последние
    полгода в ней отсутствуют.
    """
    суток_канала, суток_участка = await conn.fetchrow(
        """SELECT (SELECT count(DISTINCT day) FROM feat.channel_daily
                    WHERE day > $1::date - 365 AND day <= $1::date),
                  (SELECT count(DISTINCT day) FROM feat.section_daily
                    WHERE day > $1::date - 365 AND day <= $1::date)""", as_of)

    # Первый прогон после миграции 022: таблица пуста или отстала. Догоняем год
    # целиком — на стенде это 41 секунда и 662 596 строк, один раз. Дальше
    # хватает двух суток, как и свёртке по участку.
    догоняли_год = суток_канала < суток_участка
    начало = 365 if догоняли_год else 1
    обновлено = await conn.fetchval(
        "SELECT feat.refresh_channel_daily($1::date - $2::int, $1::date)", as_of, начало)
    return суток_канала, обновлено, догоняли_год


async def собрать(conn, as_of: datetime, section_ids: list[int] | None = None):
    """Матрица признаков: (участки, значения, диагностика).

    Значения идут строго в порядке FEATURE_NAMES — это контракт, а не удобство.
    """
    часы = Хронометр()
    суток = await часы.запрос("покрытие_свёртки", покрытие_свёртки(conn, as_of, section_ids))
    if суток == 0:
        raise RuntimeError(
            f"feat.section_daily не покрывает ни одних суток до {as_of:%d.%m.%Y}. "
            f"Девять признаков по участку вышли бы нулями, а ноль означал бы "
            f"«датчики молчали весь год». Сначала догнать свёртку: "
            f"SELECT feat.refresh_section_daily(...)")

    суток_канала, свёрнуто_каналов, догоняли_год = await часы.запрос(
        "освежить_канальную_свёртку", освежить_канальную_свёртку(conn, as_of))

    участок = {r["section_id"]: dict(r) for r in
               await часы.запрос("по_участку", conn.fetch(ПО_УЧАСТКУ, as_of, section_ids))}
    каналов = {r["section_id"]: r["каналов"] for r in
               await часы.запрос("каналов_на_участке", conn.fetch(КАНАЛОВ_НА_УЧАСТКЕ, section_ids))}
    # Участки, которых свёртка не знает: молчат дольше года. Досчитываем им
    # last_day по журналу, иначе days_since_last_reading выйдет null и участок
    # останется без объяснения ровно там, где оно очевиднее всего.
    нет_свёртки = sorted(set(каналов) - set(участок))
    досчитано = 0
    if нет_свёртки and len(нет_свёртки) <= ПРЕДЕЛ_ДОСЧЁТА:
        участок.update({r["section_id"]: {"last_day": r["last_day"]}
                        for r in await часы.запрос(
                            "последняя_запись_без_окна",
                            conn.fetch(ПОСЛЕДНЯЯ_ЗАПИСЬ_БЕЗ_ОКНА, нет_свёртки))})
        досчитано = len(нет_свёртки)

    каналы = [dict(r) for r in
              await часы.запрос("по_каналу", conn.fetch(ПО_КАНАЛУ, as_of, section_ids))]
    интервалы = {r["channel_id"]: dict(r) for r in
                 await часы.запрос("интервалы", conn.fetch(
                     ИНТЕРВАЛЫ, as_of, section_ids, ОКНО_ИНТЕРВАЛОВ_СУТОК))}

    эпизодов = await часы.запрос(
        "эпизодов", conn.fetchval("SELECT count(*) FROM smvu.fault_episode"))
    отказы_участка = ({r["section_id"]: r["отказов"] for r in
                       await часы.запрос("отказы_участка",
                                         conn.fetch(ОТКАЗЫ_ПО_УЧАСТКУ, as_of, section_ids))}
                      if эпизодов else {})
    отказы_коллектора = ({r["collector"]: r["отказов"] for r in
                          await часы.запрос("отказы_коллектора",
                                            conn.fetch(ОТКАЗЫ_ПО_КОЛЛЕКТОРУ, as_of))}
                         if эпизодов else {})

    # Канальные признаки складываем по участку, коллекторные — по коллектору.
    по_участку_каналы, по_коллектору, коллектор_участка, писало_30 = {}, {}, {}, {}
    for к in каналы:
        sid, соб = к["section_id"], к["collector"]
        коллектор_участка.setdefault(sid, соб)
        и = интервалы.get(к["channel_id"], {})
        медиана = и.get("медиана")
        медиана = медиана if медиана else None          # 0 секунд — не делитель
        фон_8н = (к["n8w"] / 8) if к["n8w"] else None   # фона нет — отношения нет
        фон_год = (к["n365"] / 52) if к["n365"] else None
        пауза = (as_of - к["last_read"]).total_seconds() if к["last_read"] else None
        всего_инт = и.get("всего_7d") or 0

        if к["n30"]:
            писало_30[sid] = писало_30.get(sid, 0) + 1
        по_участку_каналы.setdefault(sid, []).append({
            "chatter_7d": к["chatter7"],
            "undefined_share_7d": _доля(к["undefined7"], к["n7"]),
            "rate_ratio_7d": (к["n7"] / фон_8н) if фон_8н else None,
            "rate_ratio_year": (к["n7"] / фон_год) if фон_год else None,
            "gap_to_median": (и["макс_дельта_7d"] / медиана)
                             if медиана and и.get("макс_дельта_7d") is not None else None,
            "long_gap_share_7d": (и["длинных_7d"] / всего_инт)
                                 if всего_инт >= МИНИМУМ_ИНТЕРВАЛОВ else None,
            "silence_normalized": (пауза / медиана) if медиана and пауза is not None else None,
            # Три записи подряд с одинаковым значением. None — записей меньше трёх,
            # то есть проверять было не на чем.
            "freeze_3": и.get("freeze3") if и.get("интервалов_7d", 0) >= 2 else None,
        })
        if соб:
            по_коллектору[соб] = по_коллектору.get(соб, 0) + к["chatter7"]

    участки = sorted(section_ids) if section_ids else sorted(
        set(участок) | set(каналов) | set(по_участку_каналы))

    доля_года = math.tau * as_of.timetuple().tm_yday / 365.25
    season_sin, season_cos = math.sin(доля_года), math.cos(доля_года)

    значения = []
    for sid in участки:
        у = участок.get(sid, {})
        к = по_участку_каналы.get(sid, [])
        всего_каналов = каналов.get(sid)
        писавших = писало_30.get(sid, 0)
        соб = коллектор_участка.get(sid)
        последний = у.get("last_day")

        значения.append([
            float(у.get("readings_1d") or 0),
            float(у.get("readings_7d") or 0),
            float(у.get("readings_30d") or 0),
            float(у.get("readings_365d") or 0),
            float(у.get("alarms_7d") or 0),
            float(у.get("alarms_365d") or 0),
            # null — за 365 суток не писал ни разу; за окном мы не знаем, когда писал.
            float((as_of.date() - последний).days) if последний else None,
            float(всего_каналов) if всего_каналов else None,
            # Доля каналов участка, не писавших за 30 суток. Знаменатель — каналы,
            # числящиеся за участком в справочнике; числитель — те из них, от кого
            # за тридцать суток не пришло ни строки. Считать писавших за ГОД здесь
            # нельзя: участок, замолчавший месяц назад, показал бы долю 0.
            _доля(max(0, (всего_каналов or 0) - писавших), всего_каналов),
            _худший([х["chatter_7d"] for х in к], "max"),
            # Пустая smvu.fault_episode — не «отказов не было», а «не считали».
            (1.0 if отказы_участка.get(sid) else 0.0) if эпизодов else None,
            _бул(_худший([х["freeze_3"] for х in к], "max")),
            _худший([х["undefined_share_7d"] for х in к], "max"),
            # Темп: плохо, когда МАЛО. Берём самый упавший канал участка.
            _худший([х["rate_ratio_7d"] for х in к], "min"),
            _худший([х["rate_ratio_year"] for х in к], "min"),
            _худший([х["gap_to_median"] for х in к], "max"),
            _худший([х["long_gap_share_7d"] for х in к], "max"),
            _худший([х["silence_normalized"] for х in к], "max"),
            (1.0 if отказы_коллектора.get(соб) else 0.0) if эпизодов else None,
            float(по_коллектору.get(соб, 0)) if соб else None,
            season_sin,
            season_cos,
        ])

    диагностика = {
        "суток_свёртки": суток,
        "суток_канальной_свёртки": суток_канала,
        "строк_канальной_свёртки_обновлено": свёрнуто_каналов,
        "догоняли_канальную_свёртку_за_год": догоняли_год,
        "участков": len(участки),
        "каналов_писавших": len(каналы),
        "эпизодов_отказа": эпизодов,
        "участков_без_свёртки": len(нет_свёртки),
        "досчитано_по_журналу": досчитано,
        "as_of": as_of.isoformat(),
        # Разбивка по запросам. Без неё «стадия 3 тормозит» — это жалоба,
        # а не находка: чинить нечего, пока не названо, какой именно запрос.
        "мс_запросов": dict(sorted(часы.мс.items(), key=lambda п: -п[1])),
        "мс_запросов_всего": sum(часы.мс.values()),
    }
    return участки, значения, диагностика


def _бул(значение):
    """bool → 1.0/0.0, None остаётся None: контракт возит числа и пропуски."""
    return None if значение is None else float(bool(значение))


# ---------------------------------------------------------------------------
# Самопроверка
# ---------------------------------------------------------------------------

async def _selfcheck():
    """Считаем по настоящей базе на нескольких участках и проверяем форму и смысл."""
    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=900)
    try:
        as_of = await conn.fetchval("SELECT max(read_time) FROM smvu.reading_2026_06")
        образцы = [r["section_id"] for r in await conn.fetch(
            "SELECT section_id FROM ref.object_xref ORDER BY section_id LIMIT 20")]
        # Отдельно берём участки, которых свёртка не знает — они молчат дольше года.
        # Без них проверка ниже не на чем сработать: в первых двадцати таких нет.
        образцы += [r["section_id"] for r in await conn.fetch(
            """SELECT section_id FROM smvu.channel WHERE section_id IS NOT NULL
               EXCEPT SELECT section_id FROM feat.section_daily
                       WHERE day > $1::date - 365 AND day <= $1::date
               ORDER BY 1 LIMIT 3""", as_of)]

        участки, значения, диаг = await собрать(conn, as_of, образцы)

        assert len(значения) == len(участки) == len(образцы), (len(значения), len(участки))
        assert all(len(строка) == 22 for строка in значения), "в строке обязано быть 22 числа"
        assert len(FEATURE_NAMES) == 22
        # Порядок: имя и число обязаны совпадать по позиции.
        имена = dict(zip(FEATURE_NAMES, значения[0]))
        assert set(имена) == set(FEATURE_NAMES)

        # Окна вложены: за неделю не может быть больше показаний, чем за месяц.
        for s, строка in zip(участки, значения):
            r1, r7, r30, r365 = строка[:4]
            assert r1 <= r7 <= r30 <= r365, (s, строка[:4])
            assert строка[4] <= строка[5], (s, "тревог за неделю больше, чем за год")
            if строка[8] is not None:
                assert 0.0 <= строка[8] <= 1.0, (s, "доля молчащих вне 0..1")
                # Ни одного показания за 30 суток — молчат все каналы участка.
                # Эта проверка поймала настоящую ошибку 16.09.2026: доля считалась
                # по каналам, писавшим за ГОД, и участок с последней записью
                # 19 суток назад выглядел как участок без единого молчащего канала.
                assert (строка[2] > 0) or строка[8] == 1.0, \
                    (s, "показаний за 30 суток нет, а доля молчащих не единица", строка[8])
            # Ни одного показания за год — значит участок молчит дольше года,
            # и «дней с последней записи» обязано это показать. Null здесь
            # означал бы «не знаем», а мы знаем: досчитали по журналу.
            # Проверка связывает два числа, а не сторожит диапазон одного.
            if строка[3] == 0:
                assert строка[6] is not None and строка[6] > 365, \
                    (s, "показаний за год нет, а дней с последней записи", строка[6])
            if строка[12] is not None:
                assert 0.0 <= строка[12] <= 1.0, (s, "доля «Неопределен» вне 0..1")
            if строка[16] is not None:
                assert 0.0 <= строка[16] <= 1.0, (s, "доля длинных интервалов вне 0..1")
            assert -1.0 <= строка[20] <= 1.0 and -1.0 <= строка[21] <= 1.0

        # Пустая таблица эпизодов обязана давать null, а не false.
        if диаг["эпизодов_отказа"] == 0:
            assert all(строка[10] is None and строка[18] is None for строка in значения), \
                "fault_30d и neighbor_fault_7d при пустой smvu.fault_episode обязаны быть null"

        # Признаки обязаны различать участки: одинаковые строки означают, что
        # мы посчитали календарь и ничего больше.
        различий = len({tuple(с[:20]) for с in значения})
        assert различий > 1, "все участки получили одинаковые признаки — это не признаки"

        print(f"selfcheck ok: {len(участки)} участков × 22 признака, "
              f"суток свёртки {диаг['суток_свёртки']}, различных строк {различий}, "
              f"без свёртки {диаг['участков_без_свёртки']}, "
              f"досчитано по журналу {диаг['досчитано_по_журналу']}")
        for имя, число in zip(FEATURE_NAMES, значения[0]):
            print(f"   {имя:24} {число}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_selfcheck()))
