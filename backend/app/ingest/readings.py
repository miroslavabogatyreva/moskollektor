"""Приём потока показаний СМВУ пачками. Задача MOS-37 (Q3.7), приёмка Ф-82.

Зовёт `POST /api/ingest/readings` (app.api.ingest). Пачка до 5000 строк ложится
одной транзакцией: строка в load.batch (tool = 'api') и показания в smvu.reading.

ЧТО ДЕЛАЕМ С НЕХОРОШИМИ СТРОКАМИ. Канала нет в smvu.channel — строку не берём
и считаем в `unknown_channel`: заглушку канала, как файловый загрузчик, поток
не заводит, иначе опечатка в ид канала у отправителя плодила бы каналы. Повтор
(тот же journal_id и read_time) — `duplicates`, ON CONFLICT DO NOTHING: отправитель
с гарантией доставки шлёт повторы законно.

ЧЕГО ПОТОК НЕ ДЕЛАЕТ. Свёртку feat.channel_daily не трогает: край данных
(app.db.КРАЙ_ДАННЫХ) остаётся краем архива, а в расчёт показания потока не идут —
модель читает свои файлы, а не базу. Поэтому НФ-73 («доходит до расчёта за
300 с») этой задачей не закрыта, закрыта Ф-82 («показания видны в журнале»).

«Время последних данных СМВУ» для экрана источников — max(finished_at) пачек
с tool = 'api' в load.batch.
"""

from app.ingest.smvu_csv import parse_number

МАКС_ПАЧКА = 5000

ВСТАВКА = """
WITH v AS (
    SELECT * FROM unnest($2::bigint[], $3::timestamptz[], $4::int[], $5::bool[], $6::text[], $7::real[])
        AS v(journal_id, read_time, channel_id, is_alarm, value_text, value_num)
), ins AS (
    INSERT INTO smvu.reading
        (journal_id, read_time, channel_id, section_id, is_alarm, value_text, value_num, source_batch)
    SELECT v.journal_id, v.read_time, v.channel_id, c.section_id, v.is_alarm, v.value_text,
           v.value_num, $1
      FROM v JOIN smvu.channel c USING (channel_id)
    ON CONFLICT DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM ins) AS inserted,
       (SELECT count(*) FROM v
         WHERE NOT EXISTS (SELECT 1 FROM smvu.channel c WHERE c.channel_id = v.channel_id)) AS unknown
"""


async def принять(conn, строки: list[dict]) -> dict:
    """строки: [{journal_id, read_time (aware datetime), channel_id, is_alarm, value}]."""
    n = len(строки)
    assert 0 < n <= МАКС_ПАЧКА, n
    async with conn.transaction():
        batch = await conn.fetchval(
            "INSERT INTO load.batch (object_name, tool, planned_rows, stage) "
            "VALUES ('Поток СМВУ', 'api', $1, 'production') RETURNING id", n)
        r = await conn.fetchrow(
            ВСТАВКА,
            batch,
            [с["journal_id"] for с in строки], [с["read_time"] for с in строки],
            [с["channel_id"] for с in строки], [с["is_alarm"] for с in строки],
            [с["value"] for с in строки], [parse_number(с["value"]) for с in строки],
        )
        принято, чужих = r["inserted"], r["unknown"]
        await conn.execute(
            "UPDATE load.batch SET loaded_rows = $2, failed_rows = $3, finished_at = now() WHERE id = $1",
            batch, принято, чужих)
    return {"batch_id": batch, "received": n, "accepted": принято,
            "unknown_channel": чужих, "duplicates": n - принято - чужих}
