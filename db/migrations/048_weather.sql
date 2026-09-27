-- 048_weather.sql — почасовая погода по Москве. Задача MOS-36 (Q3.6), приёмка Ф-85.
-- Идёт после 047 (последняя в origin/master на момент ветки; 048 занят за MOS-36
-- по договорённости с соседней сессией 27.09.2026).
--
-- Таблица — ровно та, что в docs/HLD.md разд. 11.3. Пишет её задача планировщика
-- (backend/app/ingest/weather.py) раз в час; расчёт наружу не ходит.
--
-- «Время последнего успешного получения» из Ф-85 — это max(fetched_at): задача
-- обновляет fetched_at у каждого часа, который забрала, даже если значения
-- не изменились. Отдельной таблицы под состояние источника нет — хватает этой.

CREATE SCHEMA IF NOT EXISTS ext;

CREATE TABLE ext.weather_hourly (
    observed_at  timestamptz NOT NULL,
    is_forecast  boolean     NOT NULL,   -- false наблюдение, true прогноз
    temp_c       real,
    humidity_pct real,
    precip_mm    real,
    pressure_hpa real,
    source       text        NOT NULL,   -- имя службы, чтобы сменить поставщика без миграции
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observed_at, is_forecast, source)
);
COMMENT ON TABLE ext.weather_hourly IS 'Погода по центру Москвы, час на строку. Пишет app.ingest.weather из WEATHER_URL (Open-Meteo или наш эмулятор), приёмка Ф-85';
