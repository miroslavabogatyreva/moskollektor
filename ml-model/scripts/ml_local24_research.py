#!/usr/bin/env python3
"""Versioned local 24h research. Old autoresearch protocols remain untouched.

Select on 2025 Q2-Q4 only. Q1/Q2 2026 are known-data verification, not holdout.
No random split: fit, early stopping, calibration and evaluation are chronological.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "6")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ml.local24_eval import evaluate_predictions

FOLDS = [("2025-04-01", "2025-07-01"), ("2025-07-01", "2025-10-01"),
         ("2025-10-01", "2026-01-01")]
# Frozen before any validation score: one meaningful difference per candidate.
EXPERIMENTS = [
    dict(name="base", leaves=15, leaf=100),
    dict(name="history_only", leaves=15, leaf=100, family="history"),
    dict(name="short_history", leaves=15, leaf=100, family="short"),
    dict(name="capacity31", leaves=31, leaf=100),
    dict(name="regularized", leaves=15, leaf=300),
    dict(name="positive_weight", leaves=15, leaf=100, positive_weight=10),
    dict(name="recent_year", leaves=15, leaf=100, years=1),
    dict(name="recent_weight", leaves=15, leaf=100, half_life=365),
]
SERIES2 = [
    dict(name="normalized_short",leaves=15,leaf=100,family="short",engineering="normalized"),
    dict(name="context_short",leaves=15,leaf=100,family="short",engineering="context"),
    dict(name="combined_short",leaves=15,leaf=100,family="short",engineering="combined"),
    dict(name="combined_all",leaves=15,leaf=100,engineering="combined"),
]
ENGINEERING = False
KEYS = ["section_id", "collector_id", "as_of", "y", "n_events"]


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False,
                                   default=str) + "\n")


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def features_for(names, spec):
    mode=spec.get("engineering")
    if mode is None:
        names=[n for n in names if not n.startswith("eng_")]
    elif mode=="normalized":
        names=[n for n in names if not n.startswith(("eng_collector_","eng_other_sections_"))]
    elif mode=="context":
        names=[n for n in names if not n.startswith("eng_") or n.startswith(("eng_collector_","eng_other_sections_"))]
    family = spec.get("family")
    if family == "history":
        return [x for x in names if any(w in x for w in ("fail", "confirm", "since", "channel", "type"))]
    if family == "short":
        return [x for x in names if not any(w in x for w in ("90", "365"))]
    return names


def fit(frame, names, start, spec, seed=42):
    if set(names) & set(KEYS) or any(w in n for n in names for w in ("target", "future", "label")):
        raise ValueError("Target/key columns cannot be model features")
    start = pd.Timestamp(start)
    # Complete target and its >1h confirmation before each boundary.
    cal_start = start - pd.Timedelta(days=32)
    es_start = start - pd.Timedelta(days=64)
    tr = frame[frame.as_of < es_start - pd.Timedelta(days=2)]
    if spec.get("years"):
        tr = tr[tr.as_of >= start - pd.Timedelta(days=365 * spec["years"])]
    es = frame[(frame.as_of >= es_start) & (frame.as_of < cal_start - pd.Timedelta(days=2))]
    cal = frame[(frame.as_of >= cal_start) & (frame.as_of < start - pd.Timedelta(days=2))]
    if min(int(x.y.sum()) for x in (tr, es, cal)) < 2:
        raise ValueError("Insufficient positive events in fit/es/calibration")
    # Inverse-probability weights preserve the training prior with bounded memory.
    rng = np.random.default_rng(seed)
    q = min(1., 180000 / max(1, int((tr.y == 0).sum())))
    take = (tr.y.to_numpy() == 1) | (rng.random(len(tr)) < q)
    tr = tr.loc[take]
    weights = np.where(tr.y.to_numpy() == 1, 1., 1. / q)
    if spec.get("half_life"):
        age = (start - tr.as_of).dt.total_seconds().to_numpy() / 86400
        weights *= np.exp2(-age / spec["half_life"])
    names = features_for(names, spec)
    if not names: raise ValueError("Empty feature family")
    params = dict(objective="binary", metric="binary_logloss", verbosity=-1,
                  num_threads=6, seed=seed, deterministic=True, force_col_wise=True,
                  num_leaves=spec["leaves"], min_data_in_leaf=spec["leaf"],
                  learning_rate=.04, lambda_l2=5., feature_fraction=.9,
                  scale_pos_weight=spec.get("positive_weight", 1))
    b = lgb.train(params, lgb.Dataset(tr[names], tr.y, weight=weights),
                  num_boost_round=450,
                  valid_sets=[lgb.Dataset(es[names], es.y)],
                  callbacks=[lgb.early_stopping(40, verbose=False)])
    raw = b.predict(cal[names], raw_score=True, num_threads=6)
    lr = LogisticRegression(C=1., max_iter=200).fit(raw.reshape(-1, 1), cal.y)
    calibration = dict(coef=float(lr.coef_[0, 0]), intercept=float(lr.intercept_[0]))
    if not np.isfinite(list(calibration.values())).all() or calibration["coef"] <= 0:
        raise ValueError("Nonpositive calibration slope: candidate cannot preserve risk order")
    return b, names, calibration, dict(train_rows=len(tr), sampling_probability=q,
        train_end=str(tr.as_of.max()), early_stopping_start=str(es_start),
        calibration_start=str(cal_start), calibration_end=str(cal.as_of.max()),
        best_iteration=b.best_iteration)


def predict(b, names, calibration, rows):
    raw = b.predict(rows[names], raw_score=True, num_threads=6)
    z = np.clip(calibration["coef"] * raw + calibration["intercept"], -35, 35)
    return 1 / (1 + np.exp(-z))


def evaluate(rows, score, events, days=None):
    scored = rows[["section_id", "as_of"]].copy()
    scored["score"] = score
    return evaluate_predictions(rows[KEYS], scored, events=events, forecast_days=days)


def load(data):
    meta = json.loads((data / "metadata.json").read_text())
    rows = pd.read_parquet(data / "dataset.parquet").sort_values(["as_of", "section_id"])
    rows["as_of"] = pd.to_datetime(rows.as_of)
    names = meta["feature_names"]
    for n in names: rows[n] = rows[n].astype("float32")
    if ENGINEERING:
        from ml.local24_engineering import transform
        rows=transform(rows,"combined")
        names=names+[n for n in rows if n.startswith("eng_")]
    return meta, rows, names, pd.read_parquet(data / "events.parquet")


def baseline_scores(rows, names):
    # Actual names are recorded by the data protocol; fail rather than silently substitute.
    hist, recent = "confirmed_365d", "days_since_confirmation"
    if hist not in names or recent not in names: raise ValueError(f"Missing history baselines: {names}")
    return {"history365": rows[hist].to_numpy(), "recency": -rows[recent].to_numpy()}


def forecast_days(data, start, stop):
    calendar = pd.read_parquet(data / "forecast_days.parquet")
    d = pd.to_datetime(calendar["as_of"])
    return d[(d >= pd.Timestamp(start)) & (d < pd.Timestamp(stop))].tolist()


def run(data, out):
    out.mkdir(parents=True, exist_ok=False)
    meta, frame, names, events = load(data)
    write_json(out / "frozen_plan.json", dict(experiments=EXPERIMENTS,folds=FOLDS,
        primary_budget=10, dataset_sha256=digest(data / "dataset.parquet"),
        code_sha256=digest(__file__), verification=["2026Q1", "2026Q2_known_replay"],
        ensemble="arithmetic mean of individually calibrated models seeds42..46",
        stopping_days=30,calibration_days=30,purge_days=2,
        history_baseline="confirmed episode count in previous365d, not exposure-adjusted rate"))
    results = []
    for number, spec in enumerate([None] + EXPERIMENTS):
        started = time.monotonic()
        name = "baselines" if spec is None else spec["name"]
        reports = []
        for i, (start, stop) in enumerate(FOLDS):
            va = frame[(frame.as_of >= start) & (frame.as_of < stop)]
            days = forecast_days(data,start,stop)
            if spec is None:
                report = {k: evaluate(va, v, events,days) for k, v in baseline_scores(va, names).items()}
            else:
                b, feats, cal, info = fit(frame, names, start, spec)
                p = predict(b, feats, cal, va)
                report = evaluate(va, p, events,days)
                report["fit"] = info
                foldpath = out / f"{name}_fold{i}.parquet"
                va[KEYS].assign(score=p).to_parquet(foldpath, index=False)
            reports.append(report)
            print(json.dumps(dict(experiment=name, fold=i,
                precision10=None if spec is None else report["budgets"]["10"]["precision"],
                seconds=round(time.monotonic()-started, 2))), flush=True)
        result = dict(name=name, spec=spec, folds=reports, seconds=time.monotonic()-started)
        if spec:
            result["mean_precision10"] = float(np.mean([r["budgets"]["10"]["precision"] for r in reports]))
        write_json(out / f"{name}.json", result)
        results.append(result)
    winner = max(results[1:], key=lambda x:x["mean_precision10"])
    bmeans = {k:float(np.mean([r[k]["budgets"]["10"]["precision"] for r in results[0]["folds"]]))
              for k in results[0]["folds"][0]}
    best_baseline = max(bmeans, key=bmeans.get)
    fold_wins = sum(r["budgets"]["10"]["precision"] > base[best_baseline]["budgets"]["10"]["precision"]
                   for r,base in zip(winner["folds"],results[0]["folds"]))
    selection = dict(winner=winner["name"], spec=winner["spec"],
        mean_precision10=winner["mean_precision10"], baseline_means=bmeans,
        wins_folds=fold_wins, development_pass=winner["mean_precision10"]>bmeans[best_baseline] and fold_wins>=2,
        status="frozen_before_2026_verification", holdout=False)
    write_json(out / "selection.json", selection)
    print(json.dumps(selection), flush=True)


def verify(data, out, model_out):
    meta, frame, names, events = load(data)
    selection = json.loads((out / "selection.json").read_text())
    reports = []
    for start, stop, label in [("2025-10-01","2026-01-01","2025Q4_seed_stability"),
                              ("2026-01-01","2026-04-01","2026Q1_verification"),
                              ("2026-04-01","2026-06-30","2026Q2_known_replay")]:
        va = frame[(frame.as_of >= start) & (frame.as_of < stop)]
        days = forecast_days(data,start,stop)
        scores = []
        seed_reports = []
        for seed in range(42,47):
            b, feats, cal, info = fit(frame,names,start,selection["spec"],seed)
            p = predict(b,feats,cal,va)
            scores.append(p)
            seed_reports.append(dict(seed=seed,precision10=evaluate(va,p,events,days)["budgets"]["10"]["precision"]))
            print(json.dumps(dict(period=label,**seed_reports[-1])),flush=True)
            if start == "2026-04-01":
                model_out.mkdir(parents=True, exist_ok=True)
                b.save_model(str(model_out/f"booster_{seed}.txt"))
                write_json(model_out/f"calibration_{seed}.json",cal)
        mean = np.mean(scores,axis=0)
        r = dict(period=label, model=evaluate(va,mean,events,days), seeds=seed_reports,
                 baselines={k:evaluate(va,v,events,days) for k,v in baseline_scores(va,names).items()})
        va[KEYS].assign(score=mean).to_parquet(out/f"{label}_predictions.parquet",index=False)
        write_json(out/f"{label}.json",r)
        reports.append(r)
    boosters = [dict(file=f"booster_{seed}.txt",sha256=digest(model_out/f"booster_{seed}.txt"),
                     calibration=json.loads((model_out/f"calibration_{seed}.json").read_text())) for seed in range(42,47)]
    modelmeta=dict(model_version=f"lgbm-section-24h-{selection['winner']}-2026.09.23",model_format="local24.bag.v1",
        object_level="section",horizon_h=24,feature_schema="feat.local24.v1",feature_names=feats,
        directions=["sensor_failure"],trained_at=pd.Timestamp.now(tz="UTC").isoformat(),
        train_rows=info["train_rows"],holdout_precision=None,holdout_recall=None,
        holdout_median_lead_hours=None,boosters=boosters,fit=info,
        data_sha256=digest(data/"dataset.parquet"),selection=selection,
        valid_from="2026-04-01T00:00:00",
        evaluation_kind="known_data_development_replay",cadence="daily_00:00_Europe/Moscow",
        feature_builder="ml.local24_data",data_directory=str(data.resolve()),
        engineering=selection['spec'].get('engineering'),
        explains_probability=False,explanation_scope="mean_raw_tree_logit_before_calibration")
    modelmeta["sha256"]=hashlib.sha256(json.dumps(boosters,sort_keys=True).encode()).hexdigest()
    write_json(model_out/"model_meta.json",modelmeta)
    write_json(out/"verification.json",reports)


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("mode",choices=["run","verify"])
    p.add_argument("--data",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--model-out",type=Path)
    p.add_argument("--series",type=int,choices=[1,2],default=1)
    a=p.parse_args()
    if a.series==2:
        EXPERIMENTS=SERIES2
        ENGINEERING=True
    if a.mode=="run":run(a.data,a.out)
    else:
        if a.model_out is None or a.model_out.exists(): raise ValueError("Provide a new model output directory")
        verify(a.data,a.out,a.model_out)
