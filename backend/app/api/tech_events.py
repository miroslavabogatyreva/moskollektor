"""Журнал технологических событий по форме Приложения 2 ТЗ ДЖКХ. Задача
MOS-42 (Q4.5), приёмка Ф-89.

ПЯТЬ КОЛОНОК — из самого Приложения 2 (docs/tz-djkh.pdf, стр. 15), а не из
описания задачи в Jira (там их перечислили иначе): время регистрации, объект,
тип датчика, событие датчика, тип события.

СТРОКИ ЖУРНАЛА — не все is_alarm=true подряд. Заказчик 19.09.2026 сказал
«любые тревожные» на вопрос про отбор, но форма Приложения 2 сама показывает
вперемешку «Предупреждение» и «Норма» — то есть тревогу и её снятие. Поэтому
в журнал идут: (1) все is_alarm=true строки из диапазона и (2) для каждой —
первая следующая is_alarm=false строка ТОГО ЖЕ канала (снятие тревоги). Норму
без предшествующей тревоги не берём — иначе журнал утонет в строках, которых
5 092 003 против 49 392 тревог за один только июнь 2026 (решение оркестратора
23.09.2026, разбор в docs/HLD.md разд. 3.4).

ТИП СОБЫТИЯ — is_alarm, а не справочник канала. В примере Приложения 2 один
и тот же «Тип датчика» встречается и с «Предупреждение», и с «Норма» — тип
события меняется от чтения к чтению, а sensor_kind/system_kind статичны на
канал. Проверено на стенде 23.09.2026: канал 2866 (и другие) дал оба значения
is_alarm в одном месяце.

ПОТОЛОК ДИАПАЗОНА — 31 сутки. smvu.reading партиционирована помесячно (109
партиций), и LATERAL-подзапрос «следующая is_alarm=false строка канала» не
умеет обрезать партиции по нижней границе (она разная у каждой тревоги) —
только по верхней, если она константа запроса. Замер 23.09.2026 на боевом
стенде, диапазон «весь июнь» (49 392 тревоги, LATERAL ограничен верхней
границей диапазона):
  - без SET LOCAL jit = off: страница 200 строк — 4949,6 мс, из них
    3929,9 мс — JIT-компиляция плана (109 партиций дают план ценой ~2,2 млн,
    что далеко за порогом jit_optimize_above_cost=500000), а не выполнение;
  - с SET LOCAL jit = off: страница — 851,6 мс, total — 743,6 мс. Оба под
    секунду на самом широком из проверенных диапазонов, поэтому потолок —
    месяц, а не неделя или сутки.
`SET LOCAL` живёт только в транзакции — без неё либо не подействует, либо
останется на соединении в пуле и достанется чужому запросу.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require, видимые_участки
from app.db import КРАЙ_ДАННЫХ, get_conn

МСК = ZoneInfo("Europe/Moscow")

router = APIRouter(prefix="/api")

ПОТОЛОК_ДНЕЙ = 31

СОРТИРОВКА = {
    "read_time": "m.read_time",
    "object": "object_name",
    "sensor_kind": "c.sensor_kind",
    "value_text": "m.value_text",
    "event_type": "m.is_alarm",
}

_MERGED_CTE = """
WITH bounds AS (
    -- $1/$2 приходят как date, а не timestamptz: asyncpg иначе превратил бы
    -- их в полночь по часовому поясу ПРОЦЕССА api, а не по 'Europe/Moscow'
    -- (нашла 5e, MOS-42) — на стенде экспертов контейнер может стоять в UTC.
    -- ::timestamp ПЕРЕД AT TIME ZONE обязателен (нашла 5e, второй заход):
    -- у date своего AT TIME ZONE нет, и без явного ::timestamp Postgres сам
    -- приводит date к timestamptz по TimeZone СЕАНСА, а уже потом AT TIME ZONE
    -- от timestamptz отдаёт naive время — тот же сеансовый пояс входит дважды.
    -- ::timestamp — чистый календарь, без пояса вовсе; AT TIME ZONE 'Europe/Moscow'
    -- после него — единственное место, где пояс вообще участвует.
    SELECT ($1::date::timestamp AT TIME ZONE 'Europe/Moscow') AS от,
           ($2::date::timestamp AT TIME ZONE 'Europe/Moscow') AS до
),
in_range AS (
    SELECT r.journal_id, r.read_time, r.channel_id, r.section_id, r.value_text,
           true AS is_alarm
      FROM smvu.reading r, bounds
     WHERE r.is_alarm = true AND r.read_time >= bounds.от AND r.read_time < bounds.до
),
paired_normal AS (
    -- DISTINCT: если у канала подряд две тревоги, а следующая только одна
    -- Норма, LATERAL находит её для ОБЕИХ — без DISTINCT она попала бы
    -- в журнал дважды (нашла 5e, MOS-42: за 10.06.2026 11 Норм из 2735
    -- показывались бы дважды, total завышен на 11).
    SELECT DISTINCT nxt.journal_id, nxt.read_time, nxt.channel_id, nxt.section_id, nxt.value_text,
           false AS is_alarm
      FROM in_range a, bounds
      JOIN LATERAL (
          SELECT r.journal_id, r.read_time, r.channel_id, r.section_id, r.value_text
            FROM smvu.reading r
           WHERE r.channel_id = a.channel_id AND r.is_alarm = false
             AND r.read_time > a.read_time AND r.read_time < bounds.до
           ORDER BY r.read_time ASC
           LIMIT 1
      ) nxt ON true
),
merged AS (
    SELECT * FROM in_range
    UNION ALL
    SELECT * FROM paired_normal
)
"""

# LEFT JOIN намеренно: канал без участка (section_id NULL, либо участка нет
# в ref.object_xref) — это тоже тревога, её не теряем, object уходит null.
_ОТ_MERGED = """
  FROM merged m
  JOIN smvu.channel c             ON c.channel_id = m.channel_id
  LEFT JOIN ref.object_xref x     ON x.section_id = m.section_id
  LEFT JOIN asset.func_location l ON l.id = x.func_location_id
 WHERE ($3::text IS NULL OR c.sensor_kind = $3)
   AND ($4::text IS NULL OR (CASE WHEN m.is_alarm THEN 'Предупреждение' ELSE 'Норма' END) = $4)
   AND ($5::text IS NULL OR l.name ILIKE '%' || $5 || '%')
   AND ($6::text IS NULL OR m.value_text ILIKE '%' || $6 || '%')
   AND ($7::int[] IS NULL OR x.section_id = ANY($7))
"""

СЧЁТ_SQL = f"{_MERGED_CTE}SELECT count(*) {_ОТ_MERGED}"

ВЫБОРКА_SQL = f"""{_MERGED_CTE}
SELECT m.journal_id, m.read_time, m.is_alarm, m.value_text, c.sensor_kind, l.name AS object_name
{_ОТ_MERGED}
"""


def _тип_события(is_alarm: bool) -> str:
    return "Предупреждение" if is_alarm else "Норма"


@router.get("/tech-events")
async def list_tech_events(
    from_: date | None = Query(
        None, alias="from", description="начало периода, включительно"
    ),
    to: date | None = Query(
        None, description="конец периода, включительно — весь день целиком"
    ),
    sensor_kind: str | None = Query(
        None, description="точное совпадение с диспетчерским названием"
    ),
    event_type: str | None = Query(None, pattern="^(Предупреждение|Норма)$"),
    object: str | None = Query(None, description="подстрока в имени объекта"),
    value_text: str | None = Query(None, description="подстрока в значении датчика"),
    sort: str = Query("read_time", description="/".join(СОРТИРОВКА)),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("tech_events.read")),
):
    if sort not in СОРТИРОВКА:
        raise HTTPException(422, f"sort должен быть одним из: {', '.join(СОРТИРОВКА)}")

    if to is None:
        край = await conn.fetchval(КРАЙ_ДАННЫХ)
        to = край.date() if край else datetime.now(МСК).date()
    if from_ is None:
        from_ = to - timedelta(days=1)

    if (to - from_).days > ПОТОЛОК_ДНЕЙ:
        raise HTTPException(
            422, f"диапазон не длиннее {ПОТОЛОК_ДНЕЙ} суток, задан {(to - from_).days}"
        )

    to_exclusive = to + timedelta(days=1)
    участки = await видимые_участки(user, conn)

    async with conn.transaction():
        # Живёт только в этой транзакции (docstring выше — цифры замера).
        await conn.execute("SET LOCAL jit = off")
        total = await conn.fetchval(
            СЧЁТ_SQL,
            from_,
            to_exclusive,
            sensor_kind,
            event_type,
            object,
            value_text,
            участки,
        )
        # Второй ключ сортировки — m.journal_id: у read_time бывают повторы
        # (несколько каналов пишут в одну секунду), без второго ключа offset
        # на разных проходах вернул бы разный набор строк (нашла 98, MOS-223,
        # тот же дефект уже был у /api/orders).
        rows = await conn.fetch(
            f"{ВЫБОРКА_SQL} ORDER BY {СОРТИРОВКА[sort]} {order.upper()}, m.journal_id {order.upper()} LIMIT $8 OFFSET $9",
            from_,
            to_exclusive,
            sensor_kind,
            event_type,
            object,
            value_text,
            участки,
            limit,
            offset,
        )

    return {
        "total": total,
        "items": [
            {
                "journal_id": r["journal_id"],
                "read_time": r["read_time"].isoformat(),
                "object": r["object_name"],
                "sensor_kind": r["sensor_kind"],
                "value_text": r["value_text"],
                "event_type": _тип_события(r["is_alarm"]),
            }
            for r in rows
        ],
    }
