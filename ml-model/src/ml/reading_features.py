"""MOS-225: auditable daily methane features from immutable customer CSVs.

Standard library only. Does not generate failure labels or modify serving models.
Run: python -m ml.reading_features --help
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

MOSCOW = timezone(timedelta(hours=3))
GAS_KIND = "Газовый датчик"
METHANE_UPPER = 15.0  # Team quality policy, not a customer measurement limit.
BATCH_MIN_CHANNELS = 10
EPOCH_MARKERS = {"01.01.1970 03:00:00", "01.01.1970 03:00:01"}
COUNTS = (
    "selected_rows",
    "epoch_marker",
    "methane_valid",
    "methane_invalid",
    "methane_ge1",
    "gas_alarm_text",
)


def methane(raw: str) -> tuple[float | None, str]:
    """Only finite numbers in [0, 15] enter concentration features."""
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return None, "text"
    if not math.isfinite(value):
        return None, "nonfinite"
    if value < 0:
        return None, "negative"
    if value > METHANE_UPPER:
        return None, "above_upper"
    return value, "valid"


def channel_kinds(path: Path) -> dict[int, str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        result = {}
        for row in csv.DictReader(stream):
            channel = int(row["ид_канала_данных"])
            kind = row["тип_датчика"]
            if channel in result and result[channel] != kind:
                raise ValueError(f"Conflicting dictionary kinds: {channel}")
            result[channel] = kind
        return result


def aggregate(paths: list[Path], kinds: dict[int, str], as_of: datetime):
    """Sparse gas-channel days + epoch days; current MSK day is never included.

    Full-day inputs must be supplied. Files must not overlap; repeated CSV headers
    (the customer's concatenated 2025 halves) are permitted, duplicate rows are not
    silently deduplicated. Raw text and D5 remain in the source journal unchanged.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of requires timezone")
    cutoff = as_of.astimezone(MOSCOW).date().isoformat()
    days = {}
    totals = Counter()
    files = []
    invalid_channels = defaultdict(Counter)
    for path in paths:
        counts = Counter()
        start = end = None
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            required = ("ид_канала_данных", "дата", "значение_датчика", "тревожное")
            indices = [header.index(name) for name in required]
            ci, di, vi, ai = indices
            for line, row in enumerate(reader, 2):
                if row == header:
                    counts["repeated_headers"] += 1
                    continue
                if len(row) != len(header):
                    raise ValueError(f"{path}:{line}: wrong number of columns")
                channel = int(row[ci])
                day = row[di]
                # ISO format allows cheap filtering of tens of millions of rows.
                if len(day) != 10 or day[4] != "-" or day[7] != "-":
                    raise ValueError(f"{path}:{line}: expected ISO date")
                counts["input_rows"] += 1
                if day >= cutoff:
                    counts["excluded_unfinished_or_future_day"] += 1
                    continue
                start = min(start, day) if start else day
                end = max(end, day) if end else day
                counts["included_rows"] += 1
                raw = row[vi].strip()
                epoch = raw in EPOCH_MARKERS
                if epoch:
                    counts["epoch_marker"] += 1
                kind = kinds.get(channel)
                if kind is None:
                    counts["unknown_channel_rows"] += 1
                if kind != GAS_KIND and not epoch:
                    continue
                key = (day, channel)
                if key not in days:
                    date.fromisoformat(day)  # Validate dates before emitting features.
                    days[key] = Counter()
                rec = days[key]
                rec["selected_rows"] += 1
                rec["epoch_marker"] += int(epoch)
                if kind != GAS_KIND:
                    continue
                counts["gas_rows"] += 1
                if raw == "Обнаружен газ":
                    counts["gas_alarm_text"] += 1
                    rec["gas_alarm_text"] += 1
                value, quality = methane(raw)
                counts[quality] += 1
                if quality == "text":
                    continue
                counts["gas_numeric"] += 1
                if quality != "valid":
                    rec["methane_invalid"] += 1
                    invalid_channels[str(channel)][quality] += 1
                    continue
                rec["methane_valid"] += 1
                rec["methane_sum"] += value
                rec["methane_max"] = max(rec.get("methane_max", value), value)
                if value >= 1:
                    rec["methane_ge1"] += 1
                    counts["methane_ge1"] += 1
                    counts["methane_ge1_alarm_flag"] += row[ai].lower() in {
                        "t",
                        "true",
                        "1",
                    }
        totals.update(counts)
        files.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "first_day": start,
                "last_day": end,
                "counts": dict(counts),
            }
        )
    audit = {
        "schema": "sensor-readings.v1",
        "as_of": as_of.isoformat(),
        "policy": {
            "methane_upper_inclusive": METHANE_UPPER,
            "numeric_alarm_threshold_inclusive": 1.0,
            "batch_min_channels": BATCH_MIN_CHANNELS,
            "origin": "team quality policy; calibration is a heuristic",
        },
        "files": files,
        "totals": dict(totals),
        "invalid_channels": dict(invalid_channels),
    }
    return days, audit


def export(days, audit, out: Path):
    """Flag suspected calibration, retain text alarms; never make incident labels."""
    counts = Counter(day for (day, _), rec in days.items() if rec["methane_ge1"])
    batches = {day for day, n in counts.items() if n >= BATCH_MIN_CHANNELS}
    audit["calibration_days"] = sorted(batches)
    audit["feature_rows"] = len(days)
    audit["gas_alarm_text_on_calibration_days"] = sum(
        rec["gas_alarm_text"] for (day, _), rec in days.items() if day in batches
    )
    out.mkdir(parents=True, exist_ok=True)
    with (out / "features.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "day",
                "channel_id",
                "available_at",
                *COUNTS,
                "methane_mean",
                "methane_max",
                "calibration_candidate",
            ]
        )
        for (day, channel), rec in sorted(days.items()):
            available = datetime.combine(
                date.fromisoformat(day) + timedelta(days=1), time(), MOSCOW
            )
            n = rec["methane_valid"]
            writer.writerow(
                [
                    day,
                    channel,
                    available.isoformat(),
                    *(rec[k] for k in COUNTS),
                    rec["methane_sum"] / n if n else "",
                    rec["methane_max"] if n else "",
                    int(day in batches),
                ]
            )
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", type=Path, required=True)
    parser.add_argument("--journal", type=Path, nargs="+", required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if len({p.resolve() for p in args.journal}) != len(args.journal):
        parser.error("duplicate journal paths")
    days, audit = aggregate(args.journal, channel_kinds(args.channels), args.as_of)
    export(days, audit, args.out)
    print(
        json.dumps(
            {
                "feature_rows": len(days),
                "calibration_days": len(audit["calibration_days"]),
                "totals": audit["totals"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
