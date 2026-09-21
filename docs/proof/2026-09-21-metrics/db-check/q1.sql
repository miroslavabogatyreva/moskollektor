\timing on
-- какие значения словаря D5 вообще есть в окне апрель-июнь 2026
SELECT value_text, count(*) FROM smvu.reading
 WHERE read_time >= '2026-04-01 00:00:00+03' AND read_time < '2026-07-01 00:00:00+03'
   AND value_text IN ('Неисправен','Батарея неисправна','Много неисправных устройств','Не определено','Неопределен')
 GROUP BY 1 ORDER BY 2 DESC;
-- и по всему журналу
SELECT value_text, count(*) FROM smvu.reading
 WHERE value_text IN ('Батарея неисправна','Много неисправных устройств','Не определено')
 GROUP BY 1;
-- граница окна: сколько строк ровно 2026-06-30
SELECT count(*) FROM smvu.reading
 WHERE read_time >= '2026-06-30 00:00:00+03' AND read_time < '2026-07-01 00:00:00+03';
-- последняя запись выгрузки
SELECT max(read_time) FROM smvu.reading;
