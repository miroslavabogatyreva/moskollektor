#!/usr/bin/env python3
"""Эпизоды отказа в определении модели v3: smvu.reading -> smvu.model_failure_episode.

**Зачем второй построитель.** Модель v3 обучена на метке D5 (contracts/failure.v3.json):
отказ — эпизод длиннее часа из любого значения словаря «Неисправен», «Батарея
неисправна», «Много неисправных устройств», «Не определено». Экранный построитель
(fault_episodes.py) собирает эпизоды по smvu.fault_rule — «Неисправен» у всех типов
и «Неопределен» у двух температурных, — и трёх значений модели не видит. Мерить
модель М-18…М-20 надо на той цели, на которой её учили, поэтому эпизоды словаря
модели строятся отдельно и лежат в отдельной таблице: экран их не читает
(обоснование — шапка db/migrations/030_model_failure_value.sql).

**Чем правило отличается от экранного.** Двумя вещами, обе из src/ml/failure_defs.py.
Первая: словарь не зависит от типа датчика, метку получает любой канал, включая
заглушки без типа. Вторая: «Неопределен» в словарь не входит, и такая запись
эпизод ЗАКРЫВАЕТ, а не продолжает, — у экрана для температурных датчиков наоборот.

Алгоритм сборки эпизода тот же, что у экрана и у ML-команды: порядок
`(read_time, journal_id)`, серия подряд идущих плохих записей — один эпизод,
закрывает его первая запись с любым другим значением, незакрытый мерится до
конца архива. Эталон этой логики на Python — `эпизоды()` ниже: по нему самопроверка
сверяет SQL на живых каналах, и его же проверяют тесты без базы.
"""
import argparse
import asyncio
import os
import time
from datetime import datetime

# Каналы, у которых в журнале есть хоть одно значение словаря. Словарь не зависит
# от типа датчика, поэтому соединения со справочником каналов тут нет. Индекса
# под три новых значения нет — это один проход по журналу при построении,
# а постоянный индекс на 313 млн строк ради разового прогона дороже.
КАНАЛЫ = """
SELECT DISTINCT r.channel_id
  FROM smvu.reading r
 WHERE r.value_text IN (SELECT fault_value FROM smvu.model_failure_value)
"""

# Построение для пачки каналов. $1 — каналы, $2 — конец архива. Тело — то же,
# что ПОСТРОИТЬ в fault_episodes.py; экранный запрос не параметризован нарочно:
# правка его текста меняет эпизоды, которые читает экран.
ПОСТРОИТЬ = """
WITH записи AS (
    SELECT r.channel_id, r.read_time, r.journal_id, r.value_text, r.section_id,
           coalesce(r.value_text IN (SELECT fault_value FROM smvu.model_failure_value),
                    false) AS плохо
      FROM smvu.reading r
     WHERE r.channel_id = ANY($1)
),
сосед AS (
    SELECT *,
           lag(плохо)       OVER w AS пред_плохо,
           lead(read_time)  OVER w AS след_время,
           lead(value_text) OVER w AS след_значение
      FROM записи
    WINDOW w AS (PARTITION BY channel_id ORDER BY read_time, journal_id)
),
плохие AS (
    SELECT *, NOT coalesce(пред_плохо, false) AS начало FROM сосед WHERE плохо
),
группы AS (
    SELECT *, count(*) FILTER (WHERE начало)
                 OVER (PARTITION BY channel_id ORDER BY read_time, journal_id
                       ROWS UNBOUNDED PRECEDING) AS эпизод
      FROM плохие
),
собрано AS (
    SELECT channel_id,
           min(read_time) AS started_at,
           count(*)::int  AS rows_cnt,
           (array_agg(value_text     ORDER BY read_time,      journal_id))[1]      AS fault_value,
           (array_agg(section_id     ORDER BY read_time,      journal_id))[1]      AS section_id,
           (array_agg(след_время     ORDER BY read_time DESC, journal_id DESC))[1] AS ended_at,
           (array_agg(след_значение  ORDER BY read_time DESC, journal_id DESC))[1] AS closed_by
      FROM группы GROUP BY channel_id, эпизод
)
INSERT INTO smvu.model_failure_episode
       (channel_id, section_id, started_at, ended_at, rows_cnt, closed_by,
        fault_value, spans_outage, model_version)
SELECT s.channel_id, s.section_id, s.started_at, s.ended_at, s.rows_cnt, s.closed_by,
       s.fault_value,
       EXISTS (SELECT 1 FROM smvu.data_outage o
                WHERE tstzrange(s.started_at, coalesce(s.ended_at, $2))
                   && tstzrange(o.started_at, o.ended_at)),
       v.model_version
  FROM собрано s
  JOIN smvu.model_failure_value v ON v.fault_value = s.fault_value
 -- Строго больше порога: так в src/ml/failure_defs.py (`dur > min_hours`).
 WHERE coalesce(s.ended_at, $2) - s.started_at
       > make_interval(secs => v.min_duration_seconds)
"""


def эпизоды(записи, плохие, конец: datetime, порог_сек: float) -> list[dict]:
    """Эпизоды одного канала по словарю — эталон запроса ПОСТРОИТЬ.

    записи: [(read_time, journal_id, value_text), ...] в любом порядке — сортируем
        сами: в файлах заказчика время идёт назад 741 744 раза, и порядок строк
        давал 11 454 эпизода вместо 10 416 (fault_episodes.py).
    плохие: множество значений словаря. конец: время последней записи архива.
    Возвращает эпизоды длиннее порога, строго.
    """
    итог, текущий = [], None
    for время, _, значение in sorted(записи, key=lambda r: (r[0], r[1])):
        if значение in плохие:
            if текущий is None:
                текущий = dict(started_at=время, ended_at=None, rows_cnt=0,
                               fault_value=значение, closed_by=None)
            текущий["rows_cnt"] += 1
            continue
        if текущий is not None:
            текущий.update(ended_at=время, closed_by=значение)
            итог.append(текущий)
            текущий = None
    if текущий is not None:
        итог.append(текущий)
    return [э for э in итог
            if ((э["ended_at"] or конец) - э["started_at"]).total_seconds() > порог_сек]


async def построить(conn, конец, частей: int = 16, тихо: bool = False) -> dict:
    """Построить эпизоды модели по всему архиву. Возобновляемо, как экранный построитель:
    каналы, эпизоды которых уже лежат в таблице, пропускаются."""
    t0 = time.time()
    каналы = [r["channel_id"] for r in await conn.fetch(КАНАЛЫ)]
    построенные = {r["channel_id"] for r in await conn.fetch(
        "SELECT DISTINCT channel_id FROM smvu.model_failure_episode")}
    каналы = [ч for ч in каналы if ч not in построенные]
    for k in range(частей):
        пачка = [ч for ч in каналы if ч % частей == k]
        ответ = await conn.execute(ПОСТРОИТЬ, пачка, конец)
        if not тихо:
            print(f"   модель, пачка {k + 1}/{частей}: каналов {len(пачка)}, "
                  f"{ответ.split()[-1]} эпизодов", flush=True)
    диаг = dict(await conn.fetchrow("""
        SELECT count(*) AS эпизодов, count(DISTINCT channel_id) AS каналов,
               count(*) FILTER (WHERE ended_at IS NULL) AS незакрытых
          FROM smvu.model_failure_episode"""))
    диаг["по_значениям"] = {r["fault_value"]: r["n"] for r in await conn.fetch("""
        SELECT fault_value, count(*) AS n FROM smvu.model_failure_episode
         GROUP BY 1 ORDER BY 1""")}
    диаг["секунд"] = round(time.time() - t0, 1)
    return диаг


async def _selfcheck(dsn: str, каналов: int = 40):
    """SQL против эталона `эпизоды()` на живых каналах, во временную таблицу.

    Продуктивная таблица не трогается. Каналы берём из тех, у кого есть каждое
    из значений словаря: если «Батарея неисправна» в образец не попала, проверка
    молчала бы про неё.
    """
    import asyncpg  # локально: тесты без базы импортируют модуль без пакета

    from app.ingest.fault_episodes import конец_архива

    conn = await asyncpg.connect(dsn, command_timeout=1800)
    try:
        конец = await конец_архива(conn)
        плохие = {r["fault_value"]: r["min_duration_seconds"] for r in await conn.fetch(
            "SELECT fault_value, min_duration_seconds FROM smvu.model_failure_value")}
        порог = max(плохие.values())
        assert порог == min(плохие.values()), "порог в словаре модели разный у значений"
        каналы = set()
        for значение in плохие:
            каналы |= {r["channel_id"] for r in await conn.fetch(
                "SELECT DISTINCT channel_id FROM smvu.reading WHERE value_text = $1 "
                "LIMIT $2", значение, каналов // len(плохие))}
        каналы = sorted(каналы)
        await conn.execute("""CREATE TEMP TABLE model_failure_episode
            (LIKE smvu.model_failure_episode INCLUDING ALL)""")
        await conn.execute(ПОСТРОИТЬ.replace("INSERT INTO smvu.model_failure_episode",
                                             "INSERT INTO pg_temp.model_failure_episode"),
                           каналы, конец)
        из_sql = {(r["channel_id"], r["started_at"], r["ended_at"], r["rows_cnt"],
                   r["fault_value"], r["closed_by"]) for r in await conn.fetch(
            "SELECT * FROM pg_temp.model_failure_episode")}
        из_эталона = set()
        for ч in каналы:
            записи = [(r["read_time"], r["journal_id"], r["value_text"]) for r in
                      await conn.fetch("SELECT read_time, journal_id, value_text "
                                       "FROM smvu.reading WHERE channel_id = $1", ч)]
            из_эталона |= {(ч, э["started_at"], э["ended_at"], э["rows_cnt"],
                            э["fault_value"], э["closed_by"])
                           for э in эпизоды(записи, set(плохие), конец, порог)}
        assert из_sql == из_эталона, (
            f"SQL и эталон разошлись: только в SQL {len(из_sql - из_эталона)}, "
            f"только в эталоне {len(из_эталона - из_sql)}")
        assert из_sql, f"на {len(каналы)} каналах ни одного эпизода — так не бывает"
        print(f"selfcheck ok: {len(из_sql)} эпизодов на {len(каналы)} каналах, "
              f"SQL совпал с эталоном")
    finally:
        await conn.close()


async def _main():
    p = argparse.ArgumentParser(description="Эпизоды отказа по словарю модели v3")
    p.add_argument("--частей", type=int, default=16)
    p.add_argument("--selfcheck", action="store_true", help="только самопроверка")
    args = p.parse_args()
    if args.selfcheck:
        await _selfcheck(os.environ["DATABASE_URL"])
        return

    import asyncpg

    from app.ingest.fault_episodes import конец_архива

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=3600)
    try:
        диаг = await построить(conn, await конец_архива(conn), args.частей)
        print(f"модель: {диаг['эпизодов']} эпизодов на {диаг['каналов']} каналах, "
              f"незакрытых {диаг['незакрытых']}, по значениям {диаг['по_значениям']}, "
              f"{диаг['секунд']} с")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
