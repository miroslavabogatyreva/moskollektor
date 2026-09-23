"""Frozen daily ranking evaluation for the local 24-hour D5 experiment.

Ranks are not calibrated probabilities. One alert covers one eligible section-day;
events have unique identities and are matched at most once. Naive times are Moscow.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

KEYS = ["section_id", "as_of"]
TZ = "Europe/Moscow"


def _times(values) -> pd.Series:
    values = pd.Series(pd.to_datetime(values)).reset_index(drop=True)
    if values.dt.tz is None:
        values = values.dt.tz_localize(TZ)
    else:
        values = values.dt.tz_convert(TZ)
    if values.isna().any():
        raise ValueError("Missing timestamp")
    return values


def _frame(frame: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    out = frame.copy().reset_index(drop=True)
    for col in ("section_id", "collector_id"):
        if col in out:
            vals = pd.to_numeric(out[col], errors="raise")
            if vals.isna().any() or not np.isfinite(vals).all() or (vals % 1 != 0).any():
                raise ValueError(f"Invalid {col}")
            out[col] = vals.astype("int64")
    if "as_of" in out:
        out["as_of"] = _times(out["as_of"])
        if (out.as_of != out.as_of.dt.normalize()).any():
            raise ValueError("Forecasts must be at Moscow midnight")
        if out.duplicated(KEYS).any():
            raise ValueError("Duplicate section-day")
    return out


def _ratio(num: int, den: int) -> float | None:
    return float(num / den) if den else None


def _counts(rows: pd.DataFrame) -> dict:
    selected = rows[rows.selected]
    return {
        "eligible_section_days": int(len(rows)),
        "alerts": int(len(selected)),
        "hit_section_days": int(selected.y.sum()),
        "positive_section_days": int(rows.y.sum()),
        "precision": _ratio(int(selected.y.sum()), len(selected)),
        "section_day_recall": _ratio(int(selected.y.sum()), int(rows.y.sum())),
        "matched_events": int(selected.n_events.sum()),
        "eligible_events": int(rows.n_events.sum()),
        "event_recall_eligible": _ratio(int(selected.n_events.sum()), int(rows.n_events.sum())),
    }


def evaluate_predictions(
    riskset: pd.DataFrame,
    scores: pd.DataFrame,
    *,
    events: pd.DataFrame | None = None,
    forecast_days: Iterable | None = None,
    budgets: tuple[int, ...] = (5, 10, 20),
    bootstrap_samples: int = 0,
    seed: int = 42,
) -> dict:
    """Compare ranking policies at fixed daily budgets, without choosing thresholds.

    ``scores`` must cover the exact riskset. ``events`` contains the full event
    history (event_id, section_id, collector_id, t_start), optionally is_repeat.
    Full history distinguishes first *observed* onset from repeat; not first ever.
    event_coverage/event_recall count covered unique onsets; event_recall_one_to_one
    matches only the earliest event per issued alert, never multiple events.
    With events, event_recall includes events outside the eligible riskset too;
    event_recall_eligible separately measures only eligible section-day events.
    Without events, only eligible recall is available and event_recall is None.
    ``forecast_days`` includes zero-exposure days; otherwise observed row days only.
    Optional percentile bootstrap resamples whole days, preserving within-day groups.
    """
    rows = _frame(riskset, KEYS + ["collector_id", "y", "n_events"])
    pred = _frame(scores, KEYS + ["score"])
    for col in ("y", "n_events"):
        vals = pd.to_numeric(rows[col], errors="raise")
        if vals.isna().any() or not np.isfinite(vals).all() or (vals < 0).any() or (vals % 1 != 0).any():
            raise ValueError(f"Invalid {col}")
        rows[col] = vals.astype("int64")
    if not rows.y.isin([0, 1]).all() or not (rows.y == (rows.n_events > 0)).all():
        raise ValueError("y must equal n_events > 0")
    pred["score"] = pd.to_numeric(pred.score, errors="raise")
    if not np.isfinite(pred.score).all():
        raise ValueError("Scores must be finite ranks")
    merged = rows.merge(pred[KEYS + ["score"]], on=KEYS, how="outer", indicator=True, validate="one_to_one")
    if not merged._merge.eq("both").all():
        raise ValueError("Scores must cover the exact eligible riskset")
    rows = merged.drop(columns="_merge")
    days = _times(list(forecast_days)) if forecast_days is not None else rows.as_of.drop_duplicates()
    if (days != days.dt.normalize()).any() or days.duplicated().any():
        raise ValueError("Forecast days must be unique Moscow midnights")
    days = pd.DatetimeIndex(days.sort_values())
    if not rows.as_of.isin(days).all():
        raise ValueError("Rows outside forecast_days")
    if not len(days):
        raise ValueError("No forecast days")
    if not budgets or any(not isinstance(k, int) or isinstance(k, bool) or k < 1 for k in budgets) or len(set(budgets)) != len(budgets):
        raise ValueError("Budgets must be unique positive integers")
    if not isinstance(bootstrap_samples, int) or bootstrap_samples < 0:
        raise ValueError("Invalid bootstrap_samples")

    event_rows = None
    if events is not None:
        event_rows = _frame(events, ["event_id", "section_id", "collector_id", "t_start"])
        if event_rows.event_id.isna().any() or event_rows.event_id.duplicated().any():
            raise ValueError("Events require unique nonmissing event_id")
        event_rows["t_start"] = _times(event_rows.t_start)
        if "is_repeat" not in event_rows:
            # Simultaneous first onsets are all first observed, regardless of row order.
            first = event_rows.groupby("section_id").t_start.transform("min")
            event_rows["is_repeat"] = event_rows.t_start > first
        elif not event_rows.is_repeat.isin([True, False]).all() or event_rows.is_repeat.isna().any():
            raise ValueError("Invalid is_repeat")
        # (t,t+24h]: an onset exactly at midnight belongs to the previous day.
        event_rows["as_of"] = event_rows.t_start.dt.ceil("D") - pd.Timedelta(days=1)
        event_rows = event_rows[event_rows.as_of.isin(days)].copy()
        counts = event_rows.groupby(KEYS).size().rename("observed_events").reset_index()
        audit = rows.merge(counts, on=KEYS, how="left")
        if not (audit.n_events == audit.observed_events.fillna(0)).all():
            raise ValueError("n_events disagrees with unique events in (t,t+24h]")
        association = event_rows.merge(rows[KEYS + ["collector_id"]], on=KEYS, how="inner", suffixes=("", "_risk"))
        if not (association.collector_id == association.collector_id_risk).all():
            raise ValueError("Event collector differs from riskset")

    rows = rows.sort_values(["as_of", "score", "section_id"], ascending=[True, False, True], kind="stable")
    ranks = rows.groupby("as_of").cumcount()
    result = {"n_rows": len(rows), "n_days": len(days), "timezone": TZ,
              "score_semantics": "ranking_only", "budgets": {}}
    for k in budgets:
        rows["selected"] = ranks < k
        summary = _counts(rows)
        summary["budget"] = k
        summary["event_recall"] = None
        summary["event_coverage"] = None
        summary["event_recall_one_to_one"] = None
        summary["matched_events_one_to_one"] = summary["hit_section_days"]
        summary["total_events"] = None
        summary["excluded_risk_events"] = None
        matched = None
        if event_rows is not None:
            matched = event_rows.merge(rows[KEYS + ["selected"]], on=KEYS, how="left", validate="many_to_one")
            matched["eligible"] = matched.selected.notna()
            matched["selected"] = matched.selected.eq(True)
            matched = matched.sort_values("t_start", kind="stable")
            matched["matched_one_to_one"] = matched.selected & ~matched.duplicated(KEYS)
            summary.update(total_events=len(matched), excluded_risk_events=int((~matched.eligible).sum()),
                           event_recall=_ratio(int(matched.selected.sum()), len(matched)),
                           event_coverage=_ratio(int(matched.selected.sum()), len(matched)),
                           event_recall_one_to_one=_ratio(int(matched.matched_one_to_one.sum()), len(matched)))
            if int(matched.selected.sum()) != summary["matched_events"]:
                raise ValueError("Event matching inconsistency")
            summary["by_event_kind"] = []
            for label, flag in (("first_observed", False), ("repeat", True)):
                part = matched[matched.is_repeat == flag]
                summary["by_event_kind"].append({"kind": label, "events": len(part),
                    "eligible_events": int(part.eligible.sum()), "matched_events": int(part.selected.sum()),
                    "event_recall": _ratio(int(part.selected.sum()), len(part)),
                    "event_coverage": _ratio(int(part.selected.sum()), len(part)),
                    "matched_events_one_to_one": int(part.matched_one_to_one.sum()),
                    "event_recall_one_to_one": _ratio(int(part.matched_one_to_one.sum()), len(part))})
        else:
            summary["by_event_kind"] = None
        summary["daily"] = []
        for day in days:
            item = _counts(rows[rows.as_of == day])
            item["as_of"] = day.isoformat()
            item["total_events"] = int((matched.as_of == day).sum()) if matched is not None else None
            summary["daily"].append(item)
        summary["alerts_per_forecast_day"] = summary["alerts"] / len(days)
        summary["by_collector"] = []
        collectors = set(rows.collector_id)
        if matched is not None:
            collectors |= set(matched.collector_id)
        for collector in sorted(collectors):
            item = _counts(rows[rows.collector_id == collector])
            item["collector_id"] = int(collector)
            part = matched[matched.collector_id == collector] if matched is not None else None
            item["total_events"] = len(part) if part is not None else None
            item["event_recall"] = _ratio(int(part.selected.sum()), len(part)) if part is not None else None
            item["event_coverage"] = item["event_recall"]
            item["event_recall_one_to_one"] = _ratio(int(part.matched_one_to_one.sum()), len(part)) if part is not None else None
            summary["by_collector"].append(item)
        if bootstrap_samples:
            columns = ["hit_section_days", "alerts", "positive_section_days", "matched_events", "eligible_events"]
            if matched is not None:
                columns.append("total_events")
            matrix = np.array([[d[c] for c in columns] for d in summary["daily"]], dtype=float)
            rng = np.random.default_rng(seed)
            totals = np.array([matrix[rng.integers(0, len(days), len(days))].sum(axis=0)
                               for _ in range(bootstrap_samples)])
            pairs = {"precision": (0, 1), "section_day_recall": (0, 2), "event_recall_eligible": (3, 4)}
            if matched is not None:
                pairs["event_coverage"] = (3, 5)
                pairs["event_recall_one_to_one"] = (0, 5)
            intervals = {}
            for name, (n, d) in pairs.items():
                valid = totals[:, d] > 0
                values = totals[valid, n] / totals[valid, d]
                intervals[name] = {"low": float(np.quantile(values, .025)) if len(values) else None,
                                   "high": float(np.quantile(values, .975)) if len(values) else None,
                                   "valid_resamples": int(valid.sum())}
            summary["day_bootstrap_95"] = intervals
        result["budgets"][str(k)] = summary
    return result
