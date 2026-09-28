"""Fixed MOS-225 ablation on real D5 labels; no serving changes or tuning.

Run from repository root: .venv/bin/python ml-model/scripts/mos225_precision.py
The pinned MOS-264 helper is extracted from local Git into a temporary directory.
"""

import os

for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "2")

import hashlib
import importlib.util
import io
import json
import platform
import subprocess
import tarfile
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/proof/2026-09-28-sensor-model/data"
OUT = ROOT / "ml-model/docs/mos225-precision"
REF = "76ddf89533b60253dfce909c6420432dd928bb62"
PROTOCOL = {
    "baseline_source_commit": REF,
    "target": "real D5 channel episodes; sure PPR removed; no synthetic inputs",
    "train": ["2025-02-01", "2026-01-01"],
    "validation": ["2026-01-01", "2026-04-01"],
    "retrospective": ["2026-04-01", "2026-07-01"],
    "schedule": "daily 21:00 Europe/Moscow; completed previous days only",
    "horizon_hours": 12,
    "primary_budget": 5,
    "sensitivity_budgets": [10, 20],
    "models": ["recency", "history_lr", "history_plus_readings_lr"],
    "model_parameters": "same MOS-264 Scaled LR C=1; 10% negatives with weight 10; seed 275",
    "selection": "none: fixed horizon/budgets/features, no Q1 or Q2 tuning/refit",
    "bootstrap": "paired calendar weeks; 2000 replicates; seed 225",
    "limits": [
        "Q2 previously inspected; not a fresh holdout",
        "D5 is not repair-confirmed sensor failure",
        "current active non-stub catalog reused historically",
        "no exclusions for ongoing D5, matching MOS-264 daily protocol",
        "event-time availability only; ingestion-time history absent",
        "2025-only retraining is an ablation, not an evaluation of deployed weights",
    ],
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def past_sum(a, days):
    """Row d contains complete source days [d-days, d)."""
    cs = np.concatenate([np.zeros_like(a[:1]), a.cumsum(axis=0)], axis=0)
    ix = np.arange(len(a))
    return cs[ix] - cs[np.maximum(ix - days, 0)]


def reading_matrix(channels, days, frame):
    """Compact day × (gas channels + all-zero sentinel) × features."""
    gas_ids = channels.loc[
        channels.sensor_kind == "Газовый датчик", "channel_id"
    ].to_numpy()
    mapping = {int(c): i + 1 for i, c in enumerate(gas_ids)}
    day_index = {str(d.date()): i for i, d in enumerate(days)}
    rows = frame[frame.channel_id.isin(gas_ids)].copy()
    rows = rows[rows.day.isin(day_index)]
    di = rows.day.map(day_index).to_numpy(int)
    ci = rows.channel_id.map(mapping).to_numpy(int)
    # The producer's metadata is part of the input contract, not optional.
    expected = pd.to_datetime(rows.day).dt.tz_localize("Europe/Moscow") + pd.Timedelta(
        days=1
    )
    assert (
        pd.to_datetime(rows.available_at, utc=True).to_numpy()
        == expected.dt.tz_convert("UTC").to_numpy()
    ).all()
    arrays = {}
    for name in [
        "methane_valid",
        "methane_invalid",
        "methane_ge1",
        "gas_alarm_text",
        "calibration_candidate",
        "methane_mean",
    ]:
        a = np.zeros((len(days), len(gas_ids) + 1), np.float64)
        a[di, ci] = rows[name].fillna(0).to_numpy()
        arrays[name] = a
    features, names = [], []
    for w in [7, 30]:
        n = past_sum(arrays["methane_valid"], w)
        v = past_sum(arrays["methane_mean"] * arrays["methane_valid"], w)
        present = past_sum((arrays["methane_valid"] > 0).astype(float), w)
        vals = [
            np.log1p(n),
            np.log1p(past_sum(arrays["methane_invalid"], w)),
            past_sum(arrays["methane_ge1"], w) / np.maximum(n, 1),
            v / np.maximum(n, 1),
            np.log1p(past_sum(arrays["gas_alarm_text"], w)),
            past_sum(arrays["calibration_candidate"], w),
            present,
        ]
        stems = [
            "valid_count",
            "invalid_count",
            "ge1_share",
            "mean",
            "gas_alarm_count",
            "calibration_days",
            "observed_numeric_days",
        ]
        features.extend(vals)
        names.extend(f"{n}_{w}d" for n in stems)
    return np.stack(features, axis=-1).astype(np.float32), mapping, names


def load_helper(directory):
    paths = [
        "docs/proof/2026-09-28-sensor-model/train_sensor_model.py",
        "docs/proof/2026-09-28-sensor-model/data",
        "code/predictive_metrics.py",
        "backend/app/domain/sensor_risk.py",
        "backend/app/domain/failure_sim.py",
    ]
    raw = subprocess.check_output(["git", "archive", REF, *paths], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(directory, filter="data")
    src = Path(directory) / paths[0]
    spec = importlib.util.spec_from_file_location("mos264_fixed", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DAY0 = date(2025, 1, 1)
    module.load_data()
    for name in ["channels.csv", "failures.csv", "ppr_windows.csv"]:
        assert digest(module.DATA / name) == digest(DATA / name), name
    return module


def paired_ci(days, left, right):
    weeks = pd.DatetimeIndex(days).tz_localize(None).to_period("W").astype(str)
    unique = np.unique(weeks)
    delta = np.array([(left[weeks == w] - right[weeks == w]).sum() for w in unique])
    counts = np.array([(weeks == w).sum() for w in unique])
    rng = np.random.default_rng(225)
    samples = rng.integers(len(unique), size=(2000, len(unique)))
    values = delta[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return {
        "difference": float((left - right).mean()),
        "ci95": np.quantile(values, [0.025, 0.975]).tolist(),
        "weeks": len(unique),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Save the fixed plan before model fitting or evaluation.
    (OUT / "protocol.json").write_text(json.dumps(PROTOCOL, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="mos225-precision-") as temp:
        m = load_helper(temp)
        mc, mt, mk = m.moments(lambda _: [])
        base = m.real_features(mc, mt)
        days = pd.date_range("2025-01-01", "2026-06-30", tz="Europe/Moscow")
        frame = pd.read_csv(DATA / "reading_features.csv.gz")
        extra, mapping, names = reading_matrix(m.act, days, frame)
        assert np.array_equal(mt, np.tile(m.TICKS, m.C))
        dd = np.tile(np.arange(len(days)), m.C)
        cc = np.array([mapping.get(int(c), 0) for c in m.cid])[mc]
        baseline = m.Design(mc, mt, mk, base, "real")

        class Enriched(m.Design):
            def rows(self, mask):
                x = super().rows(mask)
                more = extra[dd[mask], cc[mask]]
                # Preserve the existing MOS-264 fresh-history gate in both arms.
                return np.hstack([x, more * (1 - x[:, :1])])

        enriched = Enriched(mc, mt, mk, base, "real")
        models = {
            "history_lr": m.fit(
                baseline, m.F, 12, tuple(map(date.fromisoformat, PROTOCOL["train"]))
            ),
            "history_plus_readings_lr": m.fit(
                enriched, m.F, 12, tuple(map(date.fromisoformat, PROTOCOL["train"]))
            ),
        }
        y = m.labels(mc, mt, m.F, 12)
        result = {
            "protocol": PROTOCOL,
            "features": names,
            "environment": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "sklearn": sklearn.__version__,
            },
            "input_hashes": {
                p.name: digest(p)
                for p in [
                    DATA / n
                    for n in [
                        "channels.csv",
                        "failures.csv",
                        "ppr_windows.csv",
                        "reading_features.csv.gz",
                    ]
                ]
            },
            "script_sha256": digest(__file__),
            "fitted_models": {
                name: {
                    "coef": model.coef_.tolist(),
                    "intercept": model.intercept_,
                    "iterations": model.n_iter,
                }
                for name, model in models.items()
            },
            "coverage": {},
            "results": {},
        }
        for pname in ["train", "validation", "retrospective"]:
            period = tuple(map(date.fromisoformat, PROTOCOL[pname]))
            mask, lo, hi = m.window_mask(mt, period, 12)
            fails = m.failures_in(m.F, lo, hi, 12)
            gas_fail = {c: fs for c, fs in fails.items() if int(m.cid[c]) in mapping}
            result["coverage"][pname] = {
                "candidate_rows": int(mask.sum()),
                "positive_rows": int(y[mask].sum()),
                "events": sum(map(len, fails.values())),
                "gas_events": sum(map(len, gas_fail.values())),
                "gas_positive_rows": int(y[mask & (cc > 0)].sum()),
                "gas_event_dates": sorted(
                    {
                        pd.Timestamp(int(t), unit="s", tz="UTC")
                        .tz_convert("Europe/Moscow")
                        .date()
                        .isoformat()
                        for fs in gas_fail.values()
                        for t in fs
                    }
                ),
            }
            if pname == "train":
                continue
            scores = {
                "recency": base[mask, 1],
                "history_lr": models["history_lr"].proba(baseline.rows(mask)),
                "history_plus_readings_lr": models["history_plus_readings_lr"].proba(
                    enriched.rows(mask)
                ),
            }
            day_hits, entries = {}, {}
            mc1, mt1, yy = mc[mask], mt[mask], y[mask]
            times = np.unique(mt1)
            for name, scores1 in scores.items():
                order = np.lexsort((m.cid[mc1], -scores1, mt1))
                _, sizes = np.unique(mt1[order], return_counts=True)
                rank = np.arange(len(order)) - np.repeat(
                    np.cumsum(sizes) - sizes, sizes
                )
                entries[name] = {}
                for k in [5, 10, 20]:
                    selected = order[rank < k]
                    gas = np.isin(m.cid[mc1[selected]], list(mapping))
                    hit = np.bincount(
                        np.searchsorted(times, mt1[selected]),
                        weights=yy[selected],
                        minlength=len(times),
                    )
                    if k == 5:
                        day_hits[name] = hit / k
                    entries[name][str(k)] = {
                        "alerts": len(selected),
                        "hits": int(yy[selected].sum()),
                        "precision_at_k": float(yy[selected].mean()),
                        "event_metrics": m.exact(
                            mc1[selected], mt1[selected], fails, 12
                        ),
                        "gas_alerts": int(gas.sum()),
                        "gas_hits": int(yy[selected][gas].sum()),
                    }
                print(
                    pname,
                    name,
                    entries[name]["5"]["hits"],
                    "/",
                    entries[name]["5"]["alerts"],
                    flush=True,
                )
            dates = pd.to_datetime(times, unit="s", utc=True).tz_convert(
                "Europe/Moscow"
            )
            result["results"][pname] = {
                "methods": entries,
                "paired_precision_at_5": {
                    other: paired_ci(
                        dates, day_hits["history_plus_readings_lr"], day_hits[other]
                    )
                    for other in ["history_lr", "recency"]
                },
            }
            daily_out = pd.DataFrame({"day": dates.astype(str), **day_hits})
            daily_out.to_csv(OUT / f"{pname}-precision-at-5.csv", index=False)
        (OUT / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(result["coverage"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
