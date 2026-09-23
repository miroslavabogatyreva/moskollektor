#!/usr/bin/env python3
"""Export map sections using real customer-tree collector identities.

Normally reads the same SQL as GET /api/objects. Offline rebuilding uses supplied
registries plus the existing export solely for stable section IDs; it never
invents a collector from a tag prefix. The browser reads the live API.
"""
import argparse
import asyncio
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.domain.section_map import SECTION_MAP_SQL, map_section
from app.ingest.tag_to_section import section_key

OUT_PATH = ROOT / "frontend/public/data/sections.json"


def from_registries(channels_path, objects_path, sections):
    with open(objects_path, encoding="utf-8-sig", newline="") as stream:
        objects = {int(r["ид_объект"]): r for r in csv.DictReader(stream)}
    members = defaultdict(list)
    with open(channels_path, encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = section_key(row["тег_инженерной_системы"], row["название_датчика"])
            if key:
                members[f"{key[0]}:{key[1]}"].append(objects.get(int(row["ид_объект"])))
    output = []
    for section in sections:
        nodes = members[section["smvu_key"]]
        parents = [objects.get(int(n["родитель"])) if n else None for n in nodes]
        parents = [p if p and int(p["иерархия_уровень"]) == 2 else None for p in parents]
        ids = sorted({int(p["ид_объект"]) for p in parents if p})
        output.append(map_section({
            **section, "collector_ids": ids,
            "collector_name": objects[ids[0]]["диспетчерское_название_объекта"] if ids else None,
            "unmapped_channels": sum(p is None for p in parents),
            "kinds": sorted({n["вид_объекта"] for n in nodes if n}),
        }))
    return output


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", type=Path)
    parser.add_argument("--objects", type=Path)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    if bool(args.channels) != bool(args.objects):
        parser.error("--channels and --objects must be supplied together")
    if args.channels:
        sections = from_registries(args.channels, args.objects, json.loads(OUT_PATH.read_text()))
    else:
        import asyncpg
        if not os.environ.get("PGPASSWORD"):
            parser.error("Set PGPASSWORD, or use --channels and --objects for offline rebuilding")
        conn = await asyncpg.connect(host="127.0.0.1", port=55432, database="moskollektor",
                                    user="moskollektor", password=os.environ["PGPASSWORD"])
        try:
            sections = [map_section(r) for r in await conn.fetch(SECTION_MAP_SQL)]
        finally:
            await conn.close()
    sections.sort(key=lambda s: (s["collector"] is None, s["collector"] or 0, s["picket"], s["section_id"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sections, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(sections)} sections; {sum(s['collector'] is None for s in sections)} unresolved -> {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
