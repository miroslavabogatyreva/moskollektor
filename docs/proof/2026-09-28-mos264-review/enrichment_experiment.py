"""MOS-264: one bounded retrospective experiment on real daily aggregates.

No database writes or production inference changes. Usage:
  .venv/bin/python docs/proof/2026-09-28-mos264-review/enrichment_experiment.py \
      --daily /tmp/mos264-data-agent/channel_daily.csv \
      --out docs/proof/2026-09-28-mos264-review/enrichment-results.json
The SQL export and exact input hashes accompany the result. Q2 has already been
examined by the project and is explicitly retrospective, not a fresh holdout.
"""

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "2")
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code"))
from predictive_metrics import evaluate_alerts  # noqa: E402

MSK = "Europe/Moscow"
DAY = 86400
BASE = pd.Timestamp("2025-06-25", tz=MSK)
DAYS = pd.date_range("2025-06-25", "2026-06-30", freq="D", tz=MSK)
TICKS = DAYS.asi8 // 10**9
T = len(DAYS)
SEED = 264
BUDGET = 5


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rolling(a, window):
    """At midnight d use complete days [d-window,d), never day d."""
    cs = np.vstack([np.zeros((1, a.shape[1]), dtype=np.float32), np.cumsum(a, axis=0)])
    ix = np.arange(len(a))
    return cs[ix] - cs[np.maximum(ix - window, 0)]


def stamp(s):
    return pd.Timestamp(s, tz=MSK).timestamp()


def run(daily_path):
    begun = time.monotonic()
    check = np.array([[1], [2], [4], [8]], dtype=np.float32)
    assert rolling(check, 2).ravel().tolist() == [0, 1, 3, 6]
    changed_future = check.copy()
    changed_future[2:] = 10000
    assert np.array_equal(rolling(check, 2)[:3], rolling(changed_future, 2)[:3])
    source = ROOT / "docs/proof/2026-09-28-sensor-model/data"
    channels = pd.read_csv(source / "channels.csv", keep_default_na=False)
    act = channels[(channels.is_active == "t") & (channels.is_stub == "f")].sort_values(
        "channel_id"
    )
    ids = act.channel_id.to_numpy(int)
    pos = {c: i for i, c in enumerate(ids)}
    C = len(ids)
    tree = (
        pd.read_csv(source / "object_tree.csv", keep_default_na=False)
        .set_index("object_id")
        .to_dict("index")
    )

    def coll(oid):
        seen = set()
        while str(oid) and int(oid) not in seen:
            oid = int(oid)
            seen.add(oid)
            r = tree.get(oid)
            if r is None:
                return -1
            if int(r["level"]) == 2:
                return oid
            oid = r["parent_id"]
        return -1

    collectors = np.array([coll(v) for v in act.object_id])
    daily = pd.read_csv(daily_path)
    raw_rows = len(daily)
    last = pd.to_datetime(daily.last_read, utc=True).dt.tz_convert(MSK)
    dates = pd.to_datetime(daily.day).dt.tz_localize(MSK)
    assert ((last >= dates) & (last < dates + pd.Timedelta(days=1))).all()
    assert not daily.duplicated(["channel_id", "day"]).any()
    coverage = (
        daily.groupby(daily.day.str[:7])
        .agg(rows=("day", "size"), days=("day", "nunique"))
        .reset_index()
    )
    daily = daily[daily.channel_id.isin(ids)].copy()
    dd = ((pd.to_datetime(daily.day) - BASE.tz_localize(None)).dt.days).to_numpy()
    cc = daily.channel_id.map(pos).to_numpy(int)
    arrays = {}
    for name in ["readings_total", "fault_total", "undefined_total"]:
        a = np.zeros((T, C), np.float32)
        a[dd, cc] = daily[name].to_numpy()
        arrays[name] = a
    last_a = np.zeros((T, C), np.int64)
    last_a[dd, cc] = (
        pd.to_datetime(daily.last_read, utc=True).astype("int64").to_numpy() // 10**9
    )
    known_last = np.vstack(
        [np.zeros((1, C), np.int64), np.maximum.accumulate(last_a, axis=0)]
    )[:-1]
    observed = known_last > 0
    gap = np.where(
        observed, np.minimum((TICKS[:, None] - known_last) / DAY, 90), 90
    ).astype(np.float32)
    assert (gap >= 0).all()

    failures = pd.read_csv(source / "failures.csv").merge(
        channels[["channel_id", "object_id", "sensor_kind"]],
        on="channel_id",
        how="left",
    )
    failures["s"] = (
        pd.to_datetime(failures.started_at, utc=True).astype("int64") // 10**9
    )
    failures["e"] = pd.to_datetime(failures.ended_at, utc=True).astype("int64") // 10**9
    failures.loc[failures.ended_at.isna(), "e"] = np.iinfo(np.int64).max
    failures = failures[
        (failures.s >= stamp("2022-04-01")) & failures.channel_id.isin(ids)
    ].copy()
    windows = pd.read_csv(source / "ppr_windows.csv", keep_default_na=False)
    planned = np.zeros(len(failures), bool)
    risk_planned = np.zeros((T, C), bool)
    for w in windows[windows.match == "sure"].itertuples():
        channels_mask = (
            (act.object_id.astype(str) == str(w.object_id))
            & (act.sensor_kind == w.sensor_kind)
        ).to_numpy()
        lo, hi = stamp(w.dismantle_from), stamp(w.return_to) + DAY
        planned |= (
            (failures.object_id.astype(str) == str(w.object_id))
            & (failures.sensor_kind == w.sensor_kind)
            & (failures.s >= lo)
            & (failures.s < hi)
        ).to_numpy()
        risk_planned |= ((TICKS >= lo) & (TICKS < hi))[:, None] & channels_mask[None, :]
    removed_ppr = int(planned.sum())
    failures = failures[~planned].copy().sort_values(["channel_id", "s"])
    eligible = observed & ~risk_planned
    base = np.zeros((T, C, 6), np.float32)
    labels = np.zeros((T, C), np.uint8)
    event_rows = []
    history = {}
    for channel, group in failures.groupby("channel_id"):
        c = pos[channel]
        s = group.s.to_numpy(np.int64)
        e = group.e.to_numpy(np.int64)
        history[c] = s
        # Onset enters features only after >1h has established D5 duration.
        n = np.searchsorted(s + 3601, TICKS, side="right")
        age = np.where(n > 0, (TICKS - s[np.maximum(n - 1, 0)]) / DAY, 365)
        base[:, c, 0] = np.exp(-np.minimum(age, 365))
        base[:, c, 1] = np.exp(-np.minimum(age, 365) / 30)
        for j, w in enumerate((7, 30, 90), start=2):
            k = np.searchsorted(s, TICKS - w * DAY, side="left")
            base[:, c, j] = np.log1p(np.maximum(n - k, 0))
        for i, (start, end) in enumerate(zip(s, e)):
            eligible[:, c] &= ~((TICKS >= start + 3601) & (TICKS < end))
            # A midnight onset is assigned to preceding tick; contract (t,t+24h].
            d = int(np.searchsorted(TICKS, start, side="left") - 1)
            if 0 <= d < T and start <= TICKS[d] + DAY:
                labels[d, c] = 1
                event_rows.append(
                    {
                        "c": c,
                        "d": d,
                        "s": int(start),
                        "first": i == 0,
                        "gas": act.iloc[c].sensor_kind == "Газовый датчик",
                        "collector": int(collectors[c]),
                    }
                )
    for collector in np.unique(collectors):
        mask = collectors == collector
        fs = failures[failures.channel_id.isin(ids[mask])].s.sort_values().to_numpy()
        n = np.searchsorted(fs + 3601, TICKS, side="right") - np.searchsorted(
            fs, TICKS - 7 * DAY, side="left"
        )
        own = np.expm1(base[:, mask, 2])
        base[:, mask, 5] = np.log1p(np.maximum(n[:, None] - own, 0))
    # Known whole-archive missing date; exclude both forecast label and immediately
    # following cut rather than interpreting this whole-park silence as health.
    bad_days = DAYS.isin(pd.to_datetime(["2026-06-01", "2026-06-02"]).tz_localize(MSK))
    eligible[bad_days, :] = False
    r1 = rolling(arrays["readings_total"], 1)
    r7 = rolling(arrays["readings_total"], 7)
    r30 = rolling(arrays["readings_total"], 30)
    f7 = rolling(arrays["fault_total"], 7)
    f30 = rolling(arrays["fault_total"], 30)
    u7 = rolling(arrays["undefined_total"], 7)
    u30 = rolling(arrays["undefined_total"], 30)
    a7 = rolling((arrays["readings_total"] > 0).astype(np.float32), 7)
    a30 = rolling((arrays["readings_total"] > 0).astype(np.float32), 30)
    # A known missing whole-archive day is not a normal zero for any channel.
    # Expose its presence in each rolling window, even after the first next cut.
    missing = (DAYS == pd.Timestamp("2026-06-01", tz=MSK)).astype(np.float32)[:, None]
    missing7 = np.broadcast_to(rolling(missing, 7), (T, C))
    missing30 = np.broadcast_to(rolling(missing, 30), (T, C))
    extra_names = [
        "readings_1d",
        "readings_7d",
        "readings_30d",
        "fault_rows_7d",
        "fault_rows_30d",
        "undefined_rows_7d",
        "undefined_rows_30d",
        "observed_days_7d",
        "observed_days_30d",
        "last_read_age_days",
        "recent_rate_ratio",
        "undefined_share_7d",
        "known_archive_missing_days_7d",
        "known_archive_missing_days_30d",
    ]
    extra = np.stack(
        [np.log1p(x) for x in (r1, r7, r30, f7, f30, u7, u30, a7, a30, gap)]
        + [
            np.log1p(
                np.minimum(
                    50,
                    (r7 / (7 - missing7))
                    / (1 + (r30 - r7) / (23 - missing30 + missing7)),
                )
            ),
            u7 / (1 + r7),
            missing7,
            missing30,
        ],
        axis=2,
    )
    kinds = pd.Categorical(act.sensor_kind)
    onehot = np.eye(len(kinds.categories), dtype=np.float32)[kinds.codes]
    per = {
        "train": ("2025-08-01", "2026-01-01"),
        "select": ("2026-01-01", "2026-04-01"),
        "test_retrospective": ("2026-04-01", "2026-07-01"),
    }
    idxs = {
        name: np.where(eligible & ((TICKS >= stamp(a)) & (TICKS < stamp(b)))[:, None])
        for name, (a, b) in per.items()
    }
    rng = np.random.default_rng(SEED)
    td, tc = idxs["train"]
    yy = labels[td, tc]
    keep = (yy == 1) | (rng.random(len(yy)) < 0.1)
    td, tc, yy = td[keep], tc[keep], yy[keep]
    weights = np.where(yy > 0, 1.0, 10.0)

    def matrix(d, c, enriched):
        parts = [base[d, c], onehot[c]]
        if enriched:
            parts.insert(1, extra[d, c])
        return np.concatenate(parts, axis=1)

    models = {}
    for name, enriched, tree_model in [
        ("history_lr", False, False),
        ("enriched_lr", True, False),
        ("enriched_tree", True, True),
    ]:
        x = matrix(td, tc, enriched)
        scaler = None
        if tree_model:
            model = HistGradientBoostingClassifier(
                max_iter=60,
                max_leaf_nodes=7,
                min_samples_leaf=100,
                l2_regularization=10,
                learning_rate=0.05,
                early_stopping=False,
                random_state=SEED,
            )
        else:
            scaler = StandardScaler().fit(x, sample_weight=weights)
            x = scaler.transform(x)
            model = LogisticRegression(C=1, max_iter=1500, tol=1e-7, random_state=SEED)
        model.fit(x, yy, sample_weight=weights)
        models[name] = (model, scaler, enriched)
        print(name, "trained", round(time.monotonic() - begun, 1), flush=True)
    # Exact same daily workload and cohort for all four candidates; no threshold search.
    outputs = {}
    for pname in ["select", "test_retrospective"]:
        d, c = idxs[pname]
        scores = {"recency": base[d, c, 0]}
        for name, (model, scaler, enriched) in models.items():
            x = matrix(d, c, enriched)
            if scaler is not None:
                x = scaler.transform(x)
            scores[name] = model.predict_proba(x)[:, 1]
        events = [
            r
            for r in event_rows
            if per[pname][0] <= str(DAYS[r["d"]].date()) < per[pname][1]
        ]
        risk_events = [r for r in events if eligible[r["d"], r["c"]]]
        # Mass groups describe outcomes, never enter features or sample selection.
        for r in events:
            r["mass"] = False
        for col in set(r["collector"] for r in events):
            rr = sorted(
                [r for r in events if r["collector"] == col], key=lambda r: r["s"]
            )
            groups = []
            g = []
            for r in rr:
                if g and r["s"] - g[-1]["s"] > 3600:
                    groups.append(g)
                    g = []
                g.append(r)
            if g:
                groups.append(g)
            for g in groups:
                if len({r["c"] for r in g}) >= 5:
                    for r in g:
                        r["mass"] = True
        targets = [
            (r["c"], pd.Timestamp(r["s"], unit="s", tz=MSK).to_pydatetime())
            for r in risk_events
        ]
        metrics = {}
        daily_matches = {}
        for name, p in scores.items():
            chosen = []
            for day in np.unique(d):
                ii = np.flatnonzero(d == day)
                # Hash tie-break avoids ordering tied type/recency scores by physical id.
                tie = (ids[c[ii]].astype(np.int64) * 1103515245 + 12345) & 0x7FFFFFFF
                rank = np.lexsort((tie, -p[ii]))[:BUDGET]
                chosen.extend(ii[rank].tolist())
            chosen = np.array(chosen, int)
            alerts = [(int(c[i]), DAYS[d[i]].to_pydatetime()) for i in chosen]
            met = evaluate_alerts(
                alerts,
                targets,
                horizon_hours=1 / 3600,
                max_lead_hours=24,
                observed_object_days=len(d),
            )
            met.pop("lead_hours", None)
            picked = set(zip(d[chosen].tolist(), c[chosen].tolist()))
            used = set()
            matched = set()
            day_hits = collections.Counter()
            for event in sorted(risk_events, key=lambda r: r["s"]):
                key = (event["d"], event["c"])
                if key in picked and key not in used:
                    used.add(key)
                    matched.add(id(event))
                    day_hits[event["d"]] += 1
            assert len(matched) == met["tp"]
            daily_matches[name] = day_hits
            met["recall_including_excluded_events"] = (
                met["tp"] / len(events) if events else None
            )
            met["breakdown"] = {}
            for label, cond in [
                ("first", lambda r: r["first"]),
                ("repeat", lambda r: not r["first"]),
                ("mass", lambda r: r["mass"]),
                ("individual", lambda r: not r["mass"]),
                ("gas", lambda r: r["gas"]),
            ]:
                part = [r for r in risk_events if cond(r)]
                hit = sum(id(r) in matched for r in part)
                met["breakdown"][label] = {
                    "events": len(part),
                    "tp": hit,
                    "recall": hit / len(part) if part else None,
                }
            # At most one TP per alert; coverage may be larger with multiple onsets/day.
            met["positive_days_covered"] = int(labels[d[chosen], c[chosen]].sum())
            met["mean_predicted_probability_on_top5"] = (
                float(np.mean(p[chosen])) if name != "recency" else None
            )
            metrics[name] = met
        # Pair by complete calendar week so simultaneously affected sensors and
        # adjacent days are resampled together. This measures uncertainty only;
        # these intervals are not used to choose or tune candidates.
        weeks = collections.defaultdict(list)
        for day in np.unique(d):
            monday = DAYS[day].date().toordinal() - DAYS[day].weekday()
            weeks[monday].append(int(day))
        weeklist = list(weeks.values())
        draws = np.random.default_rng(SEED + 1).integers(
            0, len(weeklist), size=(2000, len(weeklist))
        )
        week_alerts = np.array([len(w) * BUDGET for w in weeklist])
        intervals = {}
        for candidate in ["history_lr", "enriched_lr", "enriched_tree"]:
            diffs = np.array(
                [
                    sum(
                        daily_matches[candidate][day] - daily_matches["recency"][day]
                        for day in w
                    )
                    for w in weeklist
                ]
            )
            delta = diffs[draws].sum(axis=1) / week_alerts[draws].sum(axis=1)
            intervals[candidate] = {
                "point": float(diffs.sum() / week_alerts.sum()),
                "percentile95": np.quantile(delta, [0.025, 0.975]).tolist(),
            }
        outputs[pname] = {
            "eligible_channel_days": len(d),
            "decision_days": len(np.unique(d)),
            "all_current_cohort_events": len(events),
            "eligible_events": len(risk_events),
            "events_excluded_from_risk_set": len(events) - len(risk_events),
            "metrics": metrics,
            "paired_week_bootstrap_precision_delta_vs_recency": intervals,
        }
    result = {
        "experiment": "real_daily_enrichment_fixed24h_top5",
        "seed": SEED,
        "horizon_hours": 24,
        "daily_budget": BUDGET,
        "decision_time": "00:00 Europe/Moscow; aggregates with day < decision date only",
        "training": "2025-08-01..2025-12-31; positive rows+10% negative rows (weight10)",
        "selection_policy": "four candidates fixed in advance; top5/day, no tuning on Q1 or Q2",
        "serving_status": "research only; no runtime integration",
        "event_semantics": "D5 new onset >1h excluding sure PPR; history available only onset+3601s",
        "availability_limit": "retrospective event-time causality; aggregate revisions/ingestion times not exported, historical online availability unverified",
        "retrospective_limit": "Q2 was previously examined; these are retrospective comparisons, not fresh independent acceptance",
        "data": {
            "raw_daily_rows": raw_rows,
            "active_daily_rows": len(daily),
            "first_day": "2025-06-25",
            "last_day": "2026-06-30",
            "coverage_months": coverage.to_dict("records"),
            "active_channels": C,
            "sure_ppr_removed": removed_ppr,
            "daily_sha256": digest(daily_path),
            "source_hashes": {
                p.name: digest(p)
                for p in [
                    source / "channels.csv",
                    source / "failures.csv",
                    source / "ppr_windows.csv",
                    source / "object_tree.csv",
                ]
            },
        },
        "features": {
            "base": [
                "recency1",
                "recency30",
                "count7",
                "count30",
                "count90",
                "tree_collector_count7",
                "sensor_kind",
            ],
            "added": extra_names,
        },
        "sample": {
            "train_positive_rows": int(yy.sum()),
            "train_sampled_rows": len(yy),
            "train_total_eligible": len(idxs["train"][0]),
        },
        "results": outputs,
        "versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
        "runtime_seconds": round(time.monotonic() - begun, 2),
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.daily)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["results"], ensure_ascii=False, indent=2))
