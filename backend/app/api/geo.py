"""Геометрия участков в GeoJSON и WKT. Задача MOS-45 (Q4.8), приёмка Ф-81.

Геометрия синтетическая: координат заказчик не дал, а нарисовать их разрешил сам
(«Достаточно осевых MultiLineString», 19.09.2026). Рисует её
backend/app/ingest/synthetic_geometry.py, здесь только выдача. Чтобы синтетику
не приняли за съёмку, ответ несёт `geometry_source` из geo.geo_object.source_name
и систему координат `crs`.

Оба формата отдают одни и те же координаты: у ST_AsGeoJSON по умолчанию девять
знаков после запятой, у ST_AsText пятнадцать, поэтому точность задана обоим
одна — ЗНАКОВ. Девять знаков градуса — около десятой доли миллиметра.

Область видимости — та же, что у карточки участка (MOS-107): в списке чужих
участков нет, чужой section_id — 403.
"""

import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.auth.deps import require, видимые_участки, проверить_участок
from app.db import get_conn

router = APIRouter(prefix="/api")

ЗНАКОВ = 9
CRS = "EPSG:4326"

SQL = """
SELECT x.section_id, x.smvu_key, g.source_name,
       ST_AsGeoJSON(g.geom, $3) AS geojson, ST_AsText(g.geom, $3) AS wkt
  FROM ref.object_xref x
  JOIN geo.geo_object g ON g.object_id = x.geo_object_id
 WHERE ($1::int IS NULL OR x.section_id = $1)
   AND ($2::int[] IS NULL OR x.section_id = ANY($2))
 ORDER BY x.section_id
"""


@router.get("/geo/sections")
async def geo_sections(
    fmt: str = Query(
        "geojson",
        alias="format",
        pattern="^(geojson|wkt)$",
        description="geojson или wkt",
    ),
    section_id: int | None = Query(
        None, description="один участок; без него — все видимые"
    ),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("objects.read")),
):
    if section_id is not None:
        await проверить_участок(user, conn, section_id)
    rows = await conn.fetch(SQL, section_id, await видимые_участки(user, conn), ЗНАКОВ)
    if section_id is not None and not rows:
        raise HTTPException(404, "у участка нет геометрии")
    источник = rows[0]["source_name"] if rows else "synthetic"

    if fmt == "wkt":
        return {
            "crs": CRS,
            "geometry_source": источник,
            "items": [
                {
                    "section_id": r["section_id"],
                    "smvu_key": r["smvu_key"],
                    "wkt": r["wkt"],
                }
                for r in rows
            ],
        }
    return JSONResponse(
        {
            "type": "FeatureCollection",
            "crs": CRS,
            "geometry_source": источник,
            "features": [
                {
                    "type": "Feature",
                    "id": r["section_id"],
                    "geometry": json.loads(r["geojson"]),
                    "properties": {
                        "section_id": r["section_id"],
                        "smvu_key": r["smvu_key"],
                    },
                }
                for r in rows
            ],
        },
        media_type="application/geo+json",
    )
