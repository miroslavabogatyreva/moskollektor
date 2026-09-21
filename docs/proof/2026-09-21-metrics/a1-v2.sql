set statement_timeout = 0;
set work_mem = '512MB';
create temp table ch_both as
select channel_id,
       count(*) filter (where value_text = 'Неисправен')  as n_fault,
       count(*) filter (where value_text = 'Неопределен') as n_undef
from smvu.reading
where read_time >= '2025-07-01' and read_time < '2026-07-01'
group by channel_id;
create temp table r as
select channel_id, read_time, value_text
from smvu.reading
where read_time >= '2025-07-01' and read_time < '2026-07-01'
  and channel_id in (select channel_id from ch_both where n_fault > 0 and n_undef > 0);
create index on r (channel_id, read_time);
analyze r;
create temp table u as
with s as (
  select channel_id, read_time, value_text,
         row_number() over (partition by channel_id order by read_time)
       - row_number() over (partition by channel_id, (value_text = 'Неопределен') order by read_time) as grp
  from r
)
select channel_id, min(read_time) as t0, max(read_time) as t1, count(*) as n_rec
from s where value_text = 'Неопределен' group by channel_id, grp;
analyze u;
create temp table f as select channel_id, read_time from r where value_text = 'Неисправен';
create index on f (channel_id, read_time);
analyze f;
create temp table anch as
select channel_id, t1 as t, 'undef_end'::text as kind from u where t1 < '2026-06-24'
union all
select channel_id, read_time, 'placebo_normal' from (
  select channel_id, read_time,
         row_number() over (partition by channel_id order by read_time) as rn
  from r where value_text not in ('Неопределен','Неисправен')
) z where rn % 50 = 0 and read_time < '2026-06-24';
create temp table hit as
select a.kind, a.channel_id, a.t, c.sensor_kind,
       (select min(f.read_time) from f where f.channel_id = a.channel_id and f.read_time > a.t) as nf
from anch a join smvu.channel c using (channel_id);
analyze hit;

create temp table pc as
select channel_id, sensor_kind,
  count(*) filter (where kind='undef_end') as nu,
  count(*) filter (where kind='placebo_normal') as np,
  avg(case when kind='undef_end' then (nf is not null and nf<=t+interval '7 day')::int end) as ru7,
  avg(case when kind='placebo_normal' then (nf is not null and nf<=t+interval '7 day')::int end) as rp7,
  avg(case when kind='undef_end' then (nf is not null and nf<=t+interval '1 day')::int end) as ru1,
  avg(case when kind='placebo_normal' then (nf is not null and nf<=t+interval '1 day')::int end) as rp1
from hit group by 1,2;

select 'E1 paired_by_kind', coalesce(sensor_kind,'(нет типа)'), count(*) as ch,
  count(*) filter (where ru7>rp7) as worse, count(*) filter (where ru7<rp7) as better,
  round(100*percentile_disc(0.5) within group (order by ru1)::numeric,2) as med_undef_1d,
  round(100*percentile_disc(0.5) within group (order by rp1)::numeric,2) as med_placebo_1d,
  round(100*percentile_disc(0.5) within group (order by ru7)::numeric,2) as med_undef_7d,
  round(100*percentile_disc(0.5) within group (order by rp7)::numeric,2) as med_placebo_7d
from pc where nu>=5 and np>=5 group by 2 order by 3 desc;

select 'E2 gas_concentration', count(distinct channel_id),
  sum(nu), round(100.0*max(nu)/sum(nu),2)
from pc where sensor_kind='Газовый датчик' and nu>0;

select 'E3 lag_sec_to_fault', coalesce(sensor_kind,'(нет типа)'), count(*),
  percentile_disc(0.5) within group (order by extract(epoch from (nf-t))) as med_sec,
  percentile_disc(0.9) within group (order by extract(epoch from (nf-t))) as p90_sec,
  count(*) filter (where nf <= t + interval '5 minutes') as within_5min
from hit where kind='undef_end' and nf is not null and nf <= t + interval '7 day'
group by 2 order by 3 desc limit 12;

select 'E4 lag_sec_placebo', percentile_disc(0.5) within group (order by extract(epoch from (nf-t))), count(*)
from hit where kind='placebo_normal' and nf is not null and nf <= t + interval '7 day';
