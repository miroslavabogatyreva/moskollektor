\timing off
set statement_timeout = 0;
set work_mem = '512MB';

create temp table ch_both as
select channel_id,
       count(*) as n_all,
       count(*) filter (where value_text = 'Неисправен')  as n_fault,
       count(*) filter (where value_text = 'Неопределен') as n_undef
from smvu.reading
where read_time >= '2025-07-01' and read_time < '2026-07-01'
group by channel_id;

select 'A1 channels_in_window', count(*) from ch_both;
select 'A2 ch_with_undef', count(*) from ch_both where n_undef > 0;
select 'A3 ch_with_fault', count(*) from ch_both where n_fault > 0;
select 'A4 ch_with_both', count(*) from ch_both where n_fault > 0 and n_undef > 0;

create temp table r as
select channel_id, read_time, value_text
from smvu.reading
where read_time >= '2025-07-01' and read_time < '2026-07-01'
  and channel_id in (select channel_id from ch_both where n_fault > 0 and n_undef > 0);
create index on r (channel_id, read_time);
analyze r;
select 'A5 rows_in_r', count(*) from r;
select 'A6 mix_in_r', value_text, count(*) from r group by 2 order by 3 desc limit 10;

-- серии подряд идущих «Неопределен»
create temp table u as
with s as (
  select channel_id, read_time, value_text,
         row_number() over (partition by channel_id order by read_time)
       - row_number() over (partition by channel_id, (value_text = 'Неопределен') order by read_time) as grp
  from r
)
select channel_id, min(read_time) as t0, max(read_time) as t1, count(*) as n_rec
from s
where value_text = 'Неопределен'
group by channel_id, grp;
create index on u (channel_id, t1);
analyze u;
select 'B1 series_total', count(*) from u;

create temp table uc as
select u.*,
       c.sensor_kind,
       (select r2.value_text from r r2
         where r2.channel_id = u.channel_id and r2.read_time > u.t1
         order by r2.read_time limit 1) as closed_by,
       (select r3.value_text from r r3
         where r3.channel_id = u.channel_id and r3.read_time < u.t0
         order by r3.read_time desc limit 1) as opened_after
from u join smvu.channel c using (channel_id);
analyze uc;

select 'B2 series_len_records', count(*),
       percentile_disc(0.5) within group (order by n_rec),
       percentile_disc(0.9) within group (order by n_rec),
       max(n_rec)
from uc;
select 'B3 series_dur_sec', percentile_disc(0.5) within group (order by extract(epoch from (t1-t0))),
       percentile_disc(0.9) within group (order by extract(epoch from (t1-t0))),
       max(extract(epoch from (t1-t0)))
from uc;
select 'B4 closed_by', coalesce(closed_by,'(конец окна)'), count(*),
       round(100.0*count(*)/sum(count(*)) over (), 2)
from uc group by 2 order by 3 desc limit 10;
select 'B5 opened_after', coalesce(opened_after,'(начало окна)'), count(*),
       round(100.0*count(*)/sum(count(*)) over (), 2)
from uc group by 2 order by 3 desc limit 10;
select 'B6 series_by_kind', coalesce(sensor_kind,'(нет типа)'), count(*),
       percentile_disc(0.5) within group (order by n_rec),
       round(100.0*count(*) filter (where closed_by='Норма')/count(*),2) as pct_closed_norma,
       round(100.0*count(*) filter (where closed_by='Неисправен')/count(*),2) as pct_closed_fault
from uc group by 2 order by 3 desc;

-- моменты «Неисправен»
create temp table f as select channel_id, read_time from r where value_text = 'Неисправен';
create index on f (channel_id, read_time);
analyze f;
select 'C0 fault_rows', count(*) from f;

-- якоря: конец серии «Неопределен» и плацебо — каждая 50-я нормальная запись
create temp table anch as
select channel_id, t1 as t, 'undef_end'::text as kind from uc where t1 < '2026-06-24'
union all
select channel_id, read_time, 'placebo_normal' from (
  select channel_id, read_time,
         row_number() over (partition by channel_id order by read_time) as rn
  from r where value_text not in ('Неопределен','Неисправен')
) z where rn % 50 = 0 and read_time < '2026-06-24';
analyze anch;
select 'C1 anchors', kind, count(*) from anch group by 2;

create temp table hit as
select a.kind, a.channel_id, a.t, c.sensor_kind,
       (select min(f.read_time) from f
         where f.channel_id = a.channel_id and f.read_time > a.t) as nf
from anch a join smvu.channel c using (channel_id);
analyze hit;

select 'C2 risk_all', kind, count(*),
  round(100.0*avg((nf is not null and nf <= t + interval '1 day')::int)::numeric, 3),
  round(100.0*avg((nf is not null and nf <= t + interval '3 day')::int)::numeric, 3),
  round(100.0*avg((nf is not null and nf <= t + interval '7 day')::int)::numeric, 3)
from hit group by 2 order by 2;

select 'C3 risk_by_kind', coalesce(sensor_kind,'(нет типа)'), kind, count(*),
  round(100.0*avg((nf is not null and nf <= t + interval '1 day')::int)::numeric, 3),
  round(100.0*avg((nf is not null and nf <= t + interval '3 day')::int)::numeric, 3),
  round(100.0*avg((nf is not null and nf <= t + interval '7 day')::int)::numeric, 3)
from hit group by 2, 3 order by 2, 3;

-- сутки канала: есть ли в тот же день нормальные показания
create temp table dd as
select channel_id, (read_time at time zone 'Europe/Moscow')::date as d,
       count(*) filter (where value_text = 'Неопределен') as cu,
       count(*) filter (where value_text = 'Неисправен')  as cf,
       count(*) filter (where value_text not in ('Неопределен','Неисправен')) as cn
from r group by 1,2;
select 'D1 days_with_undef', count(*),
       round(100.0*avg((cn>0)::int)::numeric,2) as pct_same_day_normal,
       round(100.0*avg((cf>0)::int)::numeric,2) as pct_same_day_fault
from dd where cu > 0;
select 'D2 days_without_undef', count(*),
       round(100.0*avg((cf>0)::int)::numeric,2) as pct_same_day_fault
from dd where cu = 0;

-- парное сравнение внутри канала (снимает перекос по болтливым каналам)
create temp table pc as
select channel_id, sensor_kind,
  count(*) filter (where kind='undef_end') as nu,
  count(*) filter (where kind='placebo_normal') as np,
  avg(case when kind='undef_end' then (nf is not null and nf<=t+interval '7 day')::int end) as ru7,
  avg(case when kind='placebo_normal' then (nf is not null and nf<=t+interval '7 day')::int end) as rp7,
  avg(case when kind='undef_end' then (nf is not null and nf<=t+interval '1 day')::int end) as ru1,
  avg(case when kind='placebo_normal' then (nf is not null and nf<=t+interval '1 day')::int end) as rp1
from hit group by 1,2;
select 'C4 paired_7d', count(*) filter (where ru7>rp7) as undef_worse,
       count(*) filter (where ru7<rp7) as undef_better,
       count(*) filter (where ru7=rp7) as equal, count(*) as channels
from pc where nu>=5 and np>=5;
select 'C5 paired_median_pct',
       round(100*percentile_disc(0.5) within group (order by ru7)::numeric,3),
       round(100*percentile_disc(0.5) within group (order by rp7)::numeric,3),
       round(100*percentile_disc(0.5) within group (order by ru1)::numeric,3),
       round(100*percentile_disc(0.5) within group (order by rp1)::numeric,3)
from pc where nu>=5 and np>=5;
