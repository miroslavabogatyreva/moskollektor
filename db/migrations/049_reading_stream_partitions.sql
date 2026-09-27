-- 049_reading_stream_partitions.sql — партиции smvu.reading под поток СМВУ.
-- Задача MOS-37 (Q3.7), приёмка Ф-82. Идёт после 048_weather.sql (MOS-36).
--
-- 004_events.sql завёл девяносто партиций, январь 2019 … июнь 2026, — ровно архив.
-- С 27.09.2026 показания приходят потоком: эмулятор СМВУ (backend/app/ingest/smvu_emulator.py)
-- шлёт архив со сдвигом +364 дня и временем «сейчас». Без партиций на эти месяцы
-- строки легли бы в smvu.reading_default, который по 004 обязан быть пустым.
--
-- Двенадцать месяцев, июль 2026 … июнь 2027: столько эмулятор может проиграть,
-- пока сдвинутый архив не кончится (30.06.2026 + 364 дня = 29.06.2027).
-- Default по 004 пуст (проверка — smvu_csv.py, «строк в партиции default»),
-- поэтому присоединение каждой партиции его не сканирует.
DO $$
DECLARE
    m date := date '2026-07-01';
BEGIN
    WHILE m < date '2027-07-01' LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS smvu.reading_%s PARTITION OF smvu.reading '
            'FOR VALUES FROM (%L) TO (%L)',
            to_char(m, 'YYYY_MM'), m, m + interval '1 month');
        m := m + interval '1 month';
    END LOOP;
END $$;
