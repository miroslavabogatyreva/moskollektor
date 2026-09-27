#!/usr/bin/env python3
"""Расчёт модели v3 на момент для worker (MOS-145) и его сверка с обучением.

    python3 scripts/ml_v3_score.py --as-of "2026-06-30 23:59:59" --out score.json
    python3 scripts/ml_v3_score.py --check "2026-05-15 12:00:00" "2026-06-30 23:59:59"

`--model` — каталог модели (по умолчанию `models/v3_collector_20260922`). `--events` — откуда отказы D5
и инциденты: `journal` (по умолчанию) — пересборка по журналу до `as_of`, `frozen` —
готовый набор `v3_final_20260920`, как до пересборки, или каталог с `incidents.parquet`
и `failures.parquet`.

`--out` — JSON `score.v3`: по коллектору последний момент решения, его 42 признака в
порядке `feature_names`, вероятность и открытое предупреждение. Collector v3 объявляет
`object_level=collector`, использует `collector_id` и добавляет всю историю `warnings`
с исходными probability/features, warning_id, opened_at/expires_at, closed_at/status.
Закрытое предупреждение сохраняется для потребителя, опоздавшего с чтением файла.
Формат — docs/releases/v3_collector_20260922/README.md.

`--check` для каждого момента сравнивает расчёт с набором `v3_final_20260920`, по которому
судили test брифа. Набор собран по всему журналу, расчёт — по окну журнала и по событиям,
пересобранным на момент (или обрезанным, с `--events frozen`). Сходиться обязаны:

1. состав моментов решения за разогрев;
2. 42 признака каждого момента, до 1e-9;
3. вероятности;
4. открытые предупреждения и все предупреждения с начала счёта `policy_start`.

Код возврата 0 — всё сошлось. Запуск из корня проекта.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ml import v3_score as S                                         # noqa: E402

REF = Path("data/03_processed/v3_final_20260920")
TOL = 1e-9
DEFAULT_MODEL = Path("models/v3_collector_20260922")


def validated_model(directory: Path):
    from ml.serving.model_store import ModelLoadError, load_model
    from ml.serving.v3_bag import FORMAT
    meta_path = directory / "model_meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError) as exc:
        raise ModelLoadError(f"Cannot read {meta_path}: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("model_format") != FORMAT:
        raise ModelLoadError(f"{directory} is not a v3-bag model; use --model {DEFAULT_MODEL}.")
    return load_model(directory)


def parse_moment(value: str) -> pd.Timestamp:
    try:
        moment = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid --as-of/--check date: {value!r}") from exc
    if pd.isna(moment) or moment.tzinfo is not None:
        raise ValueError("Dates must be finite archive-local timestamps without a timezone.")
    return moment


def reference(prepare, train, bag, meta: dict, as_of: pd.Timestamp, d_from: pd.Timestamp):
    """Моменты разогрева, их признаки и вероятности, открытые предупреждения — по набору."""
    horizon_h, thr = int(meta["horizon_h"]), float(meta["alert_threshold"])
    rows = S.feature_rows(prepare, train, Path(meta.get("reference_dataset", REF)), horizon_h)
    t = pd.to_datetime(rows["t"])
    rows = rows[(t.dt.normalize() >= S.moments_start(as_of)) & (t <= as_of)]
    rows = rows.reset_index(drop=True)
    rows["p"] = bag.predict_proba(rows[meta["feature_names"]].to_numpy(dtype=float))
    inc = prepare.load_incidents()
    in_policy = pd.to_datetime(rows["t"]).dt.normalize() >= d_from
    alerts = prepare.simulate(rows.loc[in_policy, ["pfx", "t", "p"]], inc, thr, horizon_h)
    return rows, alerts, S.open_warnings(alerts, inc, as_of, horizon_h)


def compare(got: pd.DataFrame, want: pd.DataFrame, feats: list[str]) -> list[str]:
    key = ["pfx", "t", "kind"]
    a = got.sort_values(key).reset_index(drop=True)
    b = want.sort_values(key).reset_index(drop=True)
    if len(a) != len(b) or not (a[key].astype(str).values == b[key].astype(str).values).all():
        ka = set(map(tuple, a[key].astype(str).values))
        kb = set(map(tuple, b[key].astype(str).values))
        return [f"моменты: расчёт {len(a)}, набор {len(b)}; только в расчёте "
                f"{sorted(ka - kb)[:3]}, только в наборе {sorted(kb - ka)[:3]}"]
    bad = []
    for col in feats + ["p"]:
        x, y = a[col].to_numpy(dtype=float), b[col].to_numpy(dtype=float)
        same = (np.isnan(x) & np.isnan(y)) | (np.abs(x - y) <= TOL * (1 + np.abs(y)))
        if not same.all():
            bad.append(f"{col}: {int((~same).sum())} из {len(x)}, "
                       f"макс. {np.nanmax(np.abs(x - y)):.3g}")
    return bad


def check(moments: list[str]) -> int:
    if not moments:
        raise ValueError("--check requires at least one date; no comparisons were run.")
    dates = [parse_moment(m) for m in moments]
    loaded = validated_model(S.MODEL_DIR)
    meta, bag = loaded.meta, loaded.bag
    feats = meta["feature_names"]
    prepare, train = S.load_code()
    failed = 0
    for as_of in dates:
        d_from = S.policy_start(as_of)
        started = time.perf_counter()
        out = S.score(as_of, log=lambda _m: None)
        took = time.perf_counter() - started
        want, want_alerts, opened = reference(prepare, train, bag, meta, as_of, d_from)
        bad = compare(out["_rows"], want, feats)
        if want.empty or out["_rows"].empty:
            bad.append("нет моментов для сравнения: дата вне покрытия проверочного набора")
        got_alerts = [(str(a.get("collector_id", a.get("pfx"))), a["t"]) for a in out["alerts"]]
        ref_alerts = [(a.pfx, f"{pd.Timestamp(a.t):%Y-%m-%dT%H:%M:%S}")
                      for a in want_alerts.itertuples(index=False)]
        if sorted(got_alerts) != sorted(ref_alerts):
            bad.append(f"предупреждения: расчёт {len(got_alerts)}, набор {len(ref_alerts)}")
        got_open = {str(c.get("collector_id", c.get("pfx"))): c["warning_opened_at"] for c in out["collectors"]
                    if c["warning_open"]}
        want_open = {p: f"{t:%Y-%m-%dT%H:%M:%S}" for p, t in opened.items()}
        if got_open != want_open:
            bad.append(f"открытые предупреждения: расчёт {got_open}, история {want_open}")
        failed += bool(bad)
        verdict = "СОШЛОСЬ" if not bad else "РАСХОЖДЕНИЕ: " + "; ".join(bad)
        print(f"{as_of}: {took:.1f} с, события {out['events_source']}, "
              f"счёт с {d_from:%Y-%m-%d}, моментов {len(want)}, "
              f"предупреждений {len(got_alerts)}, открыто {len(got_open)} — {verdict}",
              flush=True)
    print("РАСЧЁТ СОВПАДАЕТ С ОБУЧЕНИЕМ" if not failed else f"МОМЕНТОВ С РАСХОЖДЕНИЕМ: {failed}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--events", default="journal")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", nargs="+", metavar="DATE")
    action.add_argument("--as-of", metavar="DATE")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.check is not None and args.out is not None:
        parser.error("--out is only supported with --as-of")
    from ml.serving.model_store import ModelLoadError
    try:
        if args.check is not None:
            [parse_moment(m) for m in args.check]
        else:
            parse_moment(args.as_of)
        validated_model(args.model)
        S.MODEL_DIR = args.model
        S.EVENTS_SOURCE = args.events
        if args.check is not None:
            return check(args.check)
        started = time.perf_counter()
        out = S.score(args.as_of, log=lambda m: print(m, file=sys.stderr))
        out.pop("_rows")
        out["elapsed_s"] = round(time.perf_counter() - started, 1)
        text = json.dumps(out, ensure_ascii=False, indent=1)
        if args.out is not None:
            args.out.write_text(text + "\n")
        else:
            print(text)
        return 0
    except (ModelLoadError, OSError, ValueError) as exc:
        parser.error(str(exc))



if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
