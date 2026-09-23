"""Publish an explicit archived local24 score, without spreading or work orders."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import asyncpg

from app.domain.section_map import SECTION_MAP_SQL, map_section
from app.mlclient import client
from app.worker import publish

LOCK_ID = 48217  # same lock as the ordinary forecast worker


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def validate_score(data):
    """Reject wrong units, duplicate identities, changed rank or malformed vectors."""
    if not isinstance(data, dict):
        raise ValueError("Score must be an object")
    expected = dict(schema_version="score.local24.v1", feature_schema="feat.local24.v1",
                    object_level="section", horizon_h=24, timezone="Europe/Moscow", archive=True)
    for key, value in expected.items():
        if data.get(key) != value or type(data.get(key)) is not type(value):
            raise ValueError(f"Invalid {key}: expected {value}")
    if not isinstance(data.get("model_version"), str) or not data["model_version"]:
        raise ValueError("Missing model_version")
    if not re.fullmatch("[0-9a-f]{64}", data.get("model_sha256", "")):
        raise ValueError("Invalid model_sha256")
    names = data.get("feature_names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names) or len(set(names)) != len(names):
        raise ValueError("Invalid feature_names")
    as_of = datetime.fromisoformat(data["as_of"])
    moscow = ZoneInfo("Europe/Moscow")
    as_of = as_of.replace(tzinfo=moscow) if as_of.tzinfo is None else as_of.astimezone(moscow)
    if any((as_of.hour, as_of.minute, as_of.second, as_of.microsecond)):
        raise ValueError("as_of must be Moscow midnight")
    watermark = datetime.fromisoformat(data["data_watermark"])
    watermark = watermark.replace(tzinfo=moscow) if watermark.tzinfo is None else watermark.astimezone(moscow)
    if as_of > watermark:
        raise ValueError("as_of exceeds archived data watermark")
    rows = data.get("sections")
    if not isinstance(rows, list) or not rows:
        raise ValueError("No eligible sections")
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid section row")
        for key in ("section_id", "collector_id", "rank"):
            if type(row.get(key)) is not int or row[key] < 1:
                raise ValueError(f"Invalid {key}")
        if row["section_id"] in ids:
            raise ValueError("Duplicate section_id")
        ids.add(row["section_id"])
        if not _number(row.get("p")) or not 0 <= row["p"] <= 1:
            raise ValueError("Invalid probability")
        values = row.get("features")
        if not isinstance(values, list) or len(values) != len(names) or any(v is not None and not _number(v) for v in values):
            raise ValueError("Invalid feature vector")
    ordered = sorted(rows, key=lambda r: (-r["p"], r["section_id"]))
    if any(r["rank"] != i for i, r in enumerate(ordered, 1)):
        raise ValueError("Rank differs from probability/section_id order")
    if data.get("recommended_budget") != 10:
        raise ValueError("Expected frozen recommended_budget=10")
    return as_of, sorted(rows, key=lambda r: r["section_id"])


def validate_model(data, model):
    for field in ("model_version", "feature_schema", "object_level", "horizon_h", "feature_names"):
        if model.get(field) != data[field]:
            raise ValueError(f"GET /model mismatch: {field}")
    if model.get("sha256") != data["model_sha256"]:
        raise ValueError("GET /model mismatch: sha256")
    if model.get("directions") != ["sensor_failure"]:
        raise ValueError("GET /model mismatch: directions")


def validate_prediction(data, rows, response, run_id):
    ids = [r["section_id"] for r in rows]
    client._сверить_ответ(response, run_id, ids)
    for field in ("model_version", "horizon_h", "model_sha256"):
        if response.get(field) != data[field]:
            raise ValueError(f"POST /predict mismatch: {field}")
    if response.get("degraded") is not False:
        raise ValueError("Degraded prediction cannot publish local probability")
    predictions = response.get("predictions", [])
    if len(predictions) != 1 or predictions[0].get("direction") != "sensor_failure":
        raise ValueError("Unexpected prediction directions")
    prediction = predictions[0]
    if any(not _number(p) or not 0 <= p <= 1 for p in prediction["probability"]):
        raise ValueError("Invalid HTTP probability")
    delta = max(abs(row["p"] - p) for row, p in zip(rows, prediction["probability"]))
    if delta > 1e-12:
        raise ValueError(f"Score and HTTP probabilities differ: {delta}")
    factors = prediction.get("factors")
    if not isinstance(factors, list) or len(factors) != len(rows):
        raise ValueError("Invalid HTTP factors")
    names = {n: i for i, n in enumerate(data["feature_names"])}
    converted = []
    for row, items in zip(rows, factors):
        converted_row = []
        for item in items:
            if item.get("f") not in names or not _number(item.get("v")):
                raise ValueError("Invalid factor contribution")
            converted_row.append(dict(f=item["f"], v=row["features"][names[item["f"]]],
                                      d="up" if item["v"] > 0 else "down", contribution=item["v"]))
        converted.append(converted_row)
    return converted, delta


async def run(conn, path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    as_of, rows = validate_score(data)
    model = await asyncio.to_thread(client.get_model)
    validate_model(data, model)
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_ID):
        raise RuntimeError("Forecast worker already running")
    run_id = None
    try:
        mapping = {r["section_id"]: map_section(r)["collector"] for r in await conn.fetch(SECTION_MAP_SQL)}
        if any(mapping.get(row["section_id"]) != row["collector_id"] for row in rows):
            raise ValueError("Score section/collector differs from product mapping")
        run_id = await conn.fetchval(
            "INSERT INTO pred.run(status,as_of,model_version,full_log) VALUES('running',$1,$2,true) RETURNING run_id",
            as_of, data["model_version"])
        started = datetime.now(timezone.utc)
        response = await asyncio.to_thread(client.predict, run_id, as_of.isoformat(), 24,
            [r["section_id"] for r in rows], [r["features"] for r in rows],
            feature_names=data["feature_names"], schema_version=data["feature_schema"])
        factors, delta = validate_prediction(data, rows, response, run_id)
        metadata = {k: data.get(k) for k in ("schema_version", "feature_schema", "object_level", "horizon_h",
            "model_sha256", "timezone", "archive", "as_of", "data_watermark", "recommended_budget",
            "explanation_scope", "explains_probability", "cadence", "eligibility")}
        metadata.update(score_sha256=hashlib.sha256(raw).hexdigest(), orders_enabled=False,
                        spread_enabled=False, probability_parity_max_error=delta)
        texts = ["Прогноз нового события D5 на участке в следующие 24 часа. "
                 "Вклады признаков относятся к исходному логиту деревьев; это не доли итоговой вероятности."] * len(rows)
        async with conn.transaction():
            result = await publish.записать(conn, run_id, as_of, 24, "sensor_failure",
                [r["section_id"] for r in rows], [r["p"] for r in rows], factors, texts,
                full_log=True, spread_enabled=False)
            await conn.execute("""UPDATE pred.run SET status='done',finished_at=now(),objects_total=$2,
                objects_scored=$2,score_metadata=$3::jsonb,ms_inference=$4 WHERE run_id=$1""",
                run_id, len(rows), json.dumps(metadata), int((datetime.now(timezone.utc)-started).total_seconds()*1000))
        return dict(run_id=run_id, status="done", sections=len(rows), as_of=as_of.isoformat(),
                    model_version=data["model_version"], probability_parity_max_error=delta,
                    orders_enabled=False, spread_enabled=False, publication=result)
    except Exception as exc:
        if run_id is not None:
            await conn.execute("UPDATE pred.run SET status='failed',finished_at=now(),error_text=$2 WHERE run_id=$1",
                               run_id, str(exc)[:1000])
        raise
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", LOCK_ID)


async def main(path):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        print(json.dumps(await run(conn, path), ensure_ascii=False, allow_nan=False))
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", required=True)
    parser.add_argument("--archive", required=True, action="store_true", help="Explicit archived replay, never a scheduled live run")
    args = parser.parse_args()
    asyncio.run(main(args.score))
