"""Коллектор и место канала для справочника, который уже лежит в базе.

Зачем отдельный путь. Загрузчик пропускает справочник каналов, если тот уже залит:
под ним 313 млн показаний со ссылками на smvu.channel, перезаливка уронила бы их
внешним ключом. Поэтому на стенде коллектор у 765 каналов без пикета и колонку
location_kind (миграция 031) дозаполняем UPDATE-ом из того же файла — тем же
разбором, что и при первой заливке, второй реализации правил нет.

Чего не трогаем. picket и section_id: участок уже скопирован в каждую строку
smvu.reading, и сменить его у канала значит разойтись с журналом — проверка
`--check` загрузчика это расхождение ловит. Канал, у которого новое правило
нашло пикет ("ОД АВ 185+5"), оставляем с location_kind 'unknown' и называем
числом: участок у него появится только при полной перезаливке справочника.
"""

from .tag_to_section import collector_of, location_kind

# Одним UPDATE, а не двумя в соседних CTE: PostgreSQL не даёт двум подзапросам
# одного оператора менять одну строку — вторая правка пропала бы молча.
# Коллектор, который уже стоит, не затираем: расхождение с файлом не наше решение.
ДОЗАПОЛНИТЬ = """
WITH новое(channel_id, collector, location_kind) AS (
    SELECT * FROM unnest($1::int[], $2::text[], $3::text[])
), цель AS (
    SELECT c.channel_id,
           coalesce(c.collector, н.collector) AS collector,
           CASE WHEN н.location_kind = 'section' AND c.section_id IS NULL
                THEN c.location_kind ELSE н.location_kind END AS location_kind,
           c.collector IS NULL AND н.collector IS NOT NULL AS новый_коллектор,
           н.location_kind = 'section' AND c.section_id IS NULL AS без_участка
      FROM новое н JOIN smvu.channel c USING (channel_id)
), правка AS (
    UPDATE smvu.channel c
       SET collector = ц.collector, location_kind = ц.location_kind
      FROM цель ц
     WHERE c.channel_id = ц.channel_id
       AND (c.collector IS DISTINCT FROM ц.collector
            OR c.location_kind IS DISTINCT FROM ц.location_kind)
    RETURNING 1
)
SELECT (SELECT count(*) FROM цель WHERE новый_коллектор) AS коллектор,
       (SELECT count(*) FROM правка) AS правок,
       (SELECT count(*) FROM цель WHERE без_участка) AS без_участка
"""


async def есть_место(conn):
    """Накатана ли миграция 031.

    На чистой базе накат останавливается на 029: её самопроверка ждёт залитых каналов.
    Загрузчик поэтому может прийти раньше 031 и должен это пережить, а не упасть.
    """
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = "
        "'smvu' AND table_name = 'channel' AND column_name = 'location_kind')")


async def дозаполнить_место(conn, rows):
    """Проставить collector и location_kind уже залитым каналам. Возвращает число правок."""
    if not await есть_место(conn):
        print("место каналов: миграция 031 не накатана, дозаполнять некуда")
        return 0
    виды = dict(await conn.fetch("SELECT object_id, kind FROM smvu.object_tree"))
    ids, коллекторы, места = [], [], []
    for r in rows:
        tag, name = r["тег_инженерной_системы"], r["название_датчика"]
        объект = (r.get("ид_объект") or "").strip()
        ids.append(int(r["ид_канала_данных"]))
        коллекторы.append(collector_of(tag))
        места.append(location_kind(tag, name, виды.get(int(объект)) if объект.isdigit()
                                   else None))
    итог = await conn.fetchrow(ДОЗАПОЛНИТЬ, ids, коллекторы, места)
    print(f"место каналов: строк изменено {итог['правок']}, "
          f"из них коллектор проставлен {итог['коллектор']}")
    if итог["без_участка"]:
        print(f"место каналов: у {итог['без_участка']} каналов пикет найден новым правилом, "
              f"а участка в базе нет — оставлены 'unknown' до перезаливки справочника")
    return итог["правок"]
