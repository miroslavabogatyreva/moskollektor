"""Моменты решения и метки протокола v3 — «одно открытое предупреждение».

Зачем протокол. В версии 2 предупреждение выдавалось раз в сутки по каждому
коллектору, а `evaluate_alerts` считает ложным каждое сутки ложной серии
и засчитывает верную серию один раз. Замер на фолдах
(`docs/research/alert_policy_20260920/README.md`): правило без модели
«после подтверждённого отказа открыть одно предупреждение на 7 суток» даёт
Precision 0,56 при Recall 0,50, настроенная суточная модель — 0,11 при том же
Recall. Значит узкое место — способ выдачи, а не модель.

Момент решения — время, когда система вправе открыть предупреждение:

* `tick`  — конец суток `d`, как в версии 2. Суточные признаки дня `d` готовы;
* `rearm` — через `CONFIRM_H` после старта инцидента. Отказ по определению D5 —
  эпизод дольше часа, поэтому раньше этого момента система не знает, что
  начавшийся эпизод станет отказом. Суточные признаки — за последний полный
  день, то есть `d − 1`: сутки `d` ещё идут.

Метка момента `t`: инцидент того же префикса стартует в `(t, t + WINDOW_H]`.
Минимального упреждения нет — постановщик приравнял прогноз к предупреждению
(чат задачи, сообщение 422) и разрешил горизонты 1, 6 и 24 часа с обоснованием.
"""

from __future__ import annotations

WINDOW_H = 168     # предупреждение живёт 7 суток — прежняя верхняя граница окна
CONFIRM_H = 1      # порог длительности отказа D5: раньше инцидент системе не виден
# Склейка отказов коллектора в инцидент — час вместо 10 минут брифа. Отказ D5
# подтверждается через час после старта, и всё, что началось на коллекторе за этот
# час, система не может отличить от того же события: первое ещё не подтверждено.
# Для диспетчера это один выезд. При 10 минутах 15,5 % инцидентов 2025-04 … 2026-03
# стартуют в пределах часа от предыдущего — поймать их нечем при любой модели.
MERGE_MIN = 60


def build_moments(con, last_day: str, data_end_ts: str) -> None:
    """Таблица `moments` из таблиц `features`, `calendar`, `incidents`.

    Моменты идут до `last_day` включительно — до конца данных, а не до последнего дня
    с полным окном метки. Зачем: политика в работающей системе решает каждый день,
    и оценке нужны скоры моментов «хвоста», даже когда их собственная метка ещё не
    известна (`docs/research/alert_policy_20260920/README.md`, раздел 11). У таких
    моментов `y` и `n_inc_in_window` — NULL: окно `t + WINDOW_H` уходит за
    `data_end_ts`. Горизонты длиннее 168 ч размечает `prepare.label`, и конец оценки
    там ограничен отдельно. Дни-провалы тиков не дают: там суточные признаки
    посчитаны по обрывку журнала (как в версии 2).
    """
    con.execute("DROP TABLE IF EXISTS moments")
    con.execute(f"""
    CREATE TABLE moments AS
    WITH ticks AS (
        SELECT f.pfx, f.d + INTERVAL 1 DAY - INTERVAL 1 SECOND AS t,
               'tick' AS kind, f.d AS d_feat
        FROM features f JOIN calendar c USING (d)
        WHERE NOT c.is_outage AND f.d <= DATE '{last_day}'),
    rearm AS (
        SELECT i.pfx, i.t_start + INTERVAL {CONFIRM_H} HOUR AS t, 'rearm' AS kind,
               CAST(i.t_start + INTERVAL {CONFIRM_H} HOUR AS DATE) - 1 AS d_feat
        FROM incidents i
        WHERE i.t_start + INTERVAL {CONFIRM_H} HOUR < DATE '{last_day}' + INTERVAL 1 DAY),
    m AS (SELECT * FROM ticks UNION ALL SELECT * FROM rearm)
    SELECT m.pfx, m.t, m.kind, CAST(m.t AS DATE) AS d, m.d_feat,
           CASE WHEN m.t + INTERVAL {WINDOW_H} HOUR > TIMESTAMP '{data_end_ts}' THEN NULL
                WHEN count(i.pfx) > 0 THEN 1 ELSE 0 END AS y,
           CASE WHEN m.t + INTERVAL {WINDOW_H} HOUR > TIMESTAMP '{data_end_ts}' THEN NULL
                ELSE count(i.pfx) END AS n_inc_in_window
    FROM m LEFT JOIN incidents i ON i.pfx = m.pfx AND i.t_start > m.t
         AND i.t_start <= m.t + INTERVAL {WINDOW_H} HOUR
    GROUP BY m.pfx, m.t, m.kind, m.d_feat""")


def seal_failures(con, seal_ts: str) -> None:
    """Отказы без будущего: вместо `dur_h` и `closed` — `t_end`, известный к печати.

    `dur_h` незакрытого эпизода досчитан до конца выгрузки (2026-06-30) и выдаёт,
    что отказ дожил до лета. Признак вправе знать конец эпизода только если тот
    наступил, поэтому конец позже печати заменён на NULL — «ещё не закрыт».
    """
    con.execute("DROP TABLE IF EXISTS failures_sealed")
    con.execute(f"""
    CREATE TABLE failures_sealed AS
    SELECT ch, pfx, stype, t_start,
           CASE WHEN closed AND t_end_raw <= TIMESTAMP '{seal_ts}' THEN t_end_raw END AS t_end
    FROM (SELECT *, t_start + to_seconds(CAST(round(dur_h * 3600) AS BIGINT)) AS t_end_raw
          FROM failures)
    WHERE t_start <= TIMESTAMP '{seal_ts}'""")


def seal_episodes(con, source: str, chan: str, date_start: str, seal_ts: str) -> None:
    """Все эпизоды словаря D5, включая короткие, без имён и тегов каналов.

    Короткий эпизод (минуты) отказом не считается, но это сырьё для признаков
    «дребезг перед отказом». `n_bad` и `close_val` не берутся: оба известны
    только в конце эпизода и без `t_end` подсказывали бы будущее.
    """
    con.execute("DROP TABLE IF EXISTS episodes_sealed")
    con.execute(f"""
    CREATE TABLE episodes_sealed AS
    SELECT e.ch, c.pfx, c.stype, e.t_start,
           CASE WHEN e.t_end <= TIMESTAMP '{seal_ts}' THEN e.t_end END AS t_end
    FROM {source} e JOIN '{chan}' c USING (ch)
    WHERE c.pfx IS NOT NULL AND e.t_start >= DATE '{date_start}'
      AND e.t_start <= TIMESTAMP '{seal_ts}'""")
