-- 022. Суточная свёртка по каналу. Задача Q3.8 (MOS-99), приёмка М-21 и НФ-72.
--
-- ЗАЧЕМ. Замер стадии «сборка признаков» 17.09.2026 на стенде, срез 30.06.2026,
-- все 3 173 участка: стадия целиком 48,5 с, и половину её съедает один запрос —
-- счётчики по каналу за 7 суток, 8 недель и 365 суток, 25,7 с. Читает он
-- 60 096 602 строки smvu.reading за год. Та же свёртка по суткам даёт
-- не больше 10 720 × 365 = 3,9 млн строк — в пятнадцать раз меньше.
--
-- Это тот же приём, которым feat.section_daily уже заменила журнал для девяти
-- признаков по участку (004_events.sql, разд. 5). Здесь он повторён на уровень
-- ниже: девять признаков считаются ПО КАНАЛУ, а не по участку, и свёртка участка
-- для них не годится — из суммы по участку нельзя достать канал, который замолчал.
--
-- ЧЕГО ЭТА ТАБЛИЦА НЕ ЗАКРЫВАЕТ. Второй дорогой запрос стадии — интервалы между
-- записями канала, 22,4 с. Он считает медиану и 95-й перцентиль пауз, а перцентиль
-- из суточных сумм не выводится: нужны сами интервалы. Поэтому он читает журнал
-- как читал, и суточная свёртка ему не помощник.
--
-- ПОЧЕМУ КОЛОНКИ ИМЕННО ТАКИЕ. Ровно те, что нужны запросу ПО_КАНАЛУ
-- в backend/app/worker/features.py, и ни одной сверх: readings_total даёт n7, n30,
-- n8w и n365; fault_total и undefined_total — недельные счётчики «Неисправен»
-- и «Неопределен»; last_read — паузу до среза. Складывать сюда «на будущее» то,
-- чего никто не читает, значит удваивать время заполнения ради пустой колонки.
--
-- last_read ХРАНИТСЯ ТОЧНЫМ ВРЕМЕНЕМ, А НЕ ДАТОЙ. Признак silence_normalized —
-- это пауза до среза, делённая на медианный интервал канала. Медиана у живого
-- канала бывает в секундах, и округление паузы до суток превратило бы признак
-- в шум.

CREATE TABLE feat.channel_daily (
    channel_id      integer NOT NULL REFERENCES smvu.channel(channel_id) ON DELETE CASCADE,
    day             date    NOT NULL,
    readings_total  integer NOT NULL,
    -- Значение шестой колонки журнала СМВУ. 'Неисправен' — это потеря связи
    -- с устройством, наша целевая переменная (docs/for-ml-team.md разд. 1),
    -- 'Неопределен' — показание, которое прибор не смог классифицировать.
    fault_total     integer NOT NULL DEFAULT 0,
    undefined_total integer NOT NULL DEFAULT 0,
    last_read       timestamptz NOT NULL,
    PRIMARY KEY (channel_id, day)
);

COMMENT ON TABLE feat.channel_daily IS
    'Суточная свёртка по каналу: сколько записей, сколько «Неисправен» и «Неопределен», когда последняя. Заменяет журнал в запросе ПО_КАНАЛУ backend/app/worker/features.py';
COMMENT ON COLUMN feat.channel_daily.last_read IS
    'Точное время последней записи канала за сутки. Из него считается пауза до среза для признака silence_normalized — округление до суток сделало бы признак шумом';

-- Индекс по дате нужен заполнению: оно работает окном «вчера и сегодня»
-- и вычищает старое по дате, а не по каналу.
CREATE INDEX channel_daily_day_idx ON feat.channel_daily (day);

-- Инкрементальный пересчёт, в точности как feat.refresh_section_daily.
-- Окно по умолчанию — двое суток: результат проверки события приходит с задержкой.
--
-- ДАТА СУТОК — МОСКОВСКАЯ. read_time::date приводит по параметру TimeZone сеанса,
-- а он у базы стенда стоит Europe/Moscow (проверено 17.09.2026: SHOW TimeZone).
-- Та же зона у feat.section_daily, и расходиться этим двум таблицам нельзя:
-- признаки одного участка считаются и оттуда, и отсюда.
CREATE OR REPLACE FUNCTION feat.refresh_channel_daily(p_from date, p_to date)
RETURNS integer AS $$
DECLARE
    n integer;
BEGIN
    INSERT INTO feat.channel_daily AS d (
        channel_id, day, readings_total, fault_total, undefined_total, last_read)
    SELECT r.channel_id,
           r.read_time::date,
           count(*),
           count(*) FILTER (WHERE r.value_text = 'Неисправен'),
           count(*) FILTER (WHERE r.value_text = 'Неопределен'),
           max(r.read_time)
      FROM smvu.reading r
     WHERE r.read_time >= p_from::timestamptz
       AND r.read_time <  (p_to + 1)::timestamptz
     GROUP BY 1, 2
    ON CONFLICT (channel_id, day) DO UPDATE SET
        readings_total  = excluded.readings_total,
        fault_total     = excluded.fault_total,
        undefined_total = excluded.undefined_total,
        last_read       = excluded.last_read;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$ LANGUAGE plpgsql;

COMMENT ON FUNCTION feat.refresh_channel_daily(date, date) IS
    'Пересчитать суточную свёртку по каналу за окно дат. Зовёт её backend/app/worker/features.py перед сборкой признаков: стадия сама отвечает за свежесть своего входа';

-- Каналы-заглушки сюда попадают наравне с остальными, и это осознанно.
-- Их 1 142 из 12 627: они пишут в журнал, но в справочнике заказчика их нет,
-- section_id у них NULL. Фильтровать их здесь нельзя — свёртка канала не знает
-- про участки, а связь «канал → участок» может появиться завтра новой выгрузкой
-- справочника (так уже было 16.09.2026, миграция 018). Отфильтрует их чтение:
-- запрос признаков соединяется со smvu.channel и берёт только каналы с участком.
