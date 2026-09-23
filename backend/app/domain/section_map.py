"""Map identity comes from the customer object tree, never the tag prefix."""

SECTION_MAP_SQL = """
SELECT x.section_id, x.smvu_key,
       coalesce(array_agg(DISTINCT p.object_id ORDER BY p.object_id)
           FILTER (WHERE p.object_id IS NOT NULL), ARRAY[]::integer[]) AS collector_ids,
       min(p.name) AS collector_name,
       count(c.channel_id) FILTER (WHERE p.object_id IS NULL) AS unmapped_channels,
       coalesce(array_agg(DISTINCT n.kind ORDER BY n.kind)
           FILTER (WHERE n.kind IS NOT NULL), ARRAY[]::text[]) AS kinds
FROM ref.object_xref x
LEFT JOIN smvu.channel c ON c.section_id = x.section_id
LEFT JOIN smvu.object_tree n ON n.object_id = c.object_id
LEFT JOIN smvu.object_tree p ON p.object_id = n.parent_id AND p.level = 2
WHERE x.smvu_key IS NOT NULL
GROUP BY x.section_id, x.smvu_key
ORDER BY x.section_id
"""


def map_section(row):
    """Keep ambiguous and incomplete mappings off any single collector axis."""
    ids = list(row["collector_ids"])
    resolved = len(ids) == 1 and row["unmapped_channels"] == 0
    return {
        "section_id": row["section_id"],
        "smvu_key": row["smvu_key"],
        "collector": ids[0] if resolved else None,
        "collector_ids": ids,
        "collector_name": row["collector_name"] if resolved else None,
        "mapping_status": "resolved" if resolved else "ambiguous" if len(ids) > 1 else "unmapped",
        "picket": int(row["smvu_key"].split(":", 1)[1]),
        "kinds": list(row["kinds"]),
    }


async def get_section_mapping(conn, section_id):
    row = await conn.fetchrow(
        SECTION_MAP_SQL.replace("WHERE x.smvu_key IS NOT NULL", "WHERE x.smvu_key IS NOT NULL AND x.section_id = $1"),
        section_id,
    )
    return map_section(row) if row else {}
