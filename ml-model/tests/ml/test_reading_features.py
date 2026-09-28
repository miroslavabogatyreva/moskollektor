"""MOS-225: raw journals → causal features, without changing D5."""

import csv
import json
from datetime import datetime

import pytest
from ml.reading_features import MOSCOW, aggregate, export, methane


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0", (0.0, "valid")),
        ("1,0", (1.0, "valid")),
        ("15", (15.0, "valid")),
        ("15.01", (None, "above_upper")),
        ("-0.01", (None, "negative")),
        ("327.68", (None, "above_upper")),
        ("nan", (None, "nonfinite")),
        ("inf", (None, "nonfinite")),
        ("Обнаружен газ", (None, "text")),
        ("01.01.1970 03:00:00", (None, "text")),
    ],
)
def test_methane_policy(raw, expected):
    assert methane(raw) == expected


def journal(tmp_path, rows):
    path = tmp_path / "journal.csv"
    with path.open("w") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "ид_события",
                "ид_канала_данных",
                "дата",
                "время",
                "тревожное",
                "значение_датчика",
            ]
        )
        writer.writerows(rows)
    return path


def test_raw_csv_to_features_and_audit(tmp_path):
    kinds = {i: "Газовый датчик" for i in range(1, 12)}
    kinds[12] = "Охранный датчик"
    rows = [[i, i, "2026-01-01", "23:59:59", "f", "1,5"] for i in range(1, 11)]
    rows += [
        [11, 11, "2026-01-01", "12:00:00", "f", "327.68"],
        [12, 1, "2026-01-01", "12:00:00", "t", "Обнаружен газ"],
        [13, 12, "2026-01-01", "12:00:00", "f", "01.01.1970 03:00:00"],
        [14, 12, "2026-01-01", "12:00:01", "f", "01.01.1970 03:00:01"],
        [15, 1, "2026-01-02", "00:00:00", "f", "10"],
    ]
    path = journal(tmp_path, rows)
    original = path.read_bytes()
    days, audit = aggregate([path], kinds, datetime(2026, 1, 2, 21, tzinfo=MOSCOW))
    export(days, audit, tmp_path / "out")
    output = list(csv.DictReader((tmp_path / "out/features.csv").open()))
    gas = next(r for r in output if r["channel_id"] == "1")
    assert gas["methane_ge1"] == "1"  # independent of alarm=f
    assert gas["gas_alarm_text"] == "1"  # not erased by calibration heuristic
    assert gas["calibration_candidate"] == "1"
    assert gas["available_at"] == "2026-01-02T00:00:00+03:00"
    assert {r["day"] for r in output} == {"2026-01-01"}
    guard = next(r for r in output if r["channel_id"] == "12")
    assert guard["epoch_marker"] == "2"
    assert guard["selected_rows"] == "2"
    assert guard["methane_invalid"] == "0"
    assert (
        json.loads((tmp_path / "out/audit.json").read_text())["totals"]["above_upper"]
        == 1
    )
    assert path.read_bytes() == original


def test_bad_readings_and_non_gas_do_not_create_batch(tmp_path):
    kinds = {i: "Газовый датчик" for i in range(10)} | {10: "Другой"}
    rows = [[i, i, "2026-01-01", "12:00:00", "t", "1"] for i in range(9)]
    rows += [
        [9, 9, "2026-01-01", "12:00:00", "t", "100"],
        [10, 10, "2026-01-01", "12:00:00", "t", "2"],
    ]
    days, audit = aggregate(
        [journal(tmp_path, rows)], kinds, datetime(2026, 1, 2, tzinfo=MOSCOW)
    )
    export(days, audit, tmp_path / "out")
    assert not any(
        int(r["calibration_candidate"])
        for r in csv.DictReader((tmp_path / "out/features.csv").open())
    )


def test_naive_cutoff_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        aggregate([], {}, datetime(2026, 1, 2))  # noqa: DTZ001 - reject naive input


def test_existing_ml_aggregates_keep_epoch_as_received_not_error():
    import duckdb
    from ml import config as C
    from ml.daily import _CHANDAY_SQL
    from ml.failure_defs import D5_VALUES
    from ml.journal_vals import TIMESTAMP, category

    values = ["01.01.1970 03:00:00", "01.01.1970 03:00:01"]
    with duckdb.connect() as conn:
        conn.execute(
            "CREATE TABLE journal(ev INT, ch INT, ts TIMESTAMP, alarm BOOL, val VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO journal VALUES (?, 1, '2026-01-01 12:00:00', false, ?)",
            list(enumerate(values)),
        )
        row = conn.execute(
            _CHANDAY_SQL.format(
                src="SELECT * FROM journal",
                bad=C.VAL_BAD,
                undef=C.VAL_UNDEF,
                obes=C.VAL_DEENERGIZED,
                batt=C.VAL_BATTERY,
            )
        ).fetchone()
    assert row[2:6] == (2, 0, 0, 0)  # two received, zero bad/undefined/alarm
    assert row[-2:] == (0, None)  # not numeric does not mean missing/error
    for value in values:
        assert category(value) == TIMESTAMP
        assert value not in D5_VALUES


def test_future_day_cannot_change_past_features(tmp_path):
    rows = [[i, i, "2026-01-01", "12:00:00", "f", "1"] for i in range(9)]
    kinds = {i: "Газовый датчик" for i in range(11)}
    cut = datetime.fromisoformat("2026-01-01T21:00:00+00:00")  # next midnight MSK
    baseline, _ = aggregate([journal(tmp_path, rows)], kinds, cut)
    rows += [[10, 10, "2026-01-02", "00:00:00", "f", "1"]]
    with_future, _ = aggregate([journal(tmp_path, rows)], kinds, cut)
    assert baseline == with_future


def test_repeated_header_and_comma_numeric(tmp_path):
    path = journal(tmp_path, [[1, 1, "2026-01-01", "12:00:00", "f", "1,25"]])
    with path.open("a") as stream:
        stream.write(path.read_text().splitlines()[0] + "\n")
    days, audit = aggregate(
        [path], {1: "Газовый датчик"}, datetime(2026, 1, 2, tzinfo=MOSCOW)
    )
    assert days[("2026-01-01", 1)]["methane_sum"] == 1.25
    assert audit["totals"]["repeated_headers"] == 1
