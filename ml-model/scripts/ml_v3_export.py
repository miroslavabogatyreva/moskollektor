#!/usr/bin/env python3
"""Выгрузка модели v3 для сервера инференса (MOS-74, комментарий 14471, п. 2).

Модель — та же, что судила test брифа в основной строке отчёта взгляда: горизонт 720 ч,
обучение на незапечатанном наборе до 2026-04-01 минус зазор, 25 сидов, порог 0,63.
Код обучения — `autoresearch_v3/` с хешами `LOOK_CODE`; другой код — отказ.

    python3 scripts/ml_v3_export.py                       # в models/v3_<дата>/
    python3 scripts/ml_v3_export.py --out models/current  # явный каталог

Что пишется: `booster_<сид>.txt` (25 файлов) и `model_meta.json` — контракт
`src/ml/serving/README.md` плюс поля мешка (`src/ml/serving/v3_bag.py`). После записи
модель читается с диска и сверяется с `train.run_fold` на последних сутках обучения
(не test): расхождение вероятностей больше 1e-9 — код возврата 1.
Существующий каталог не перезаписывается.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import ml_v3_holdout as H              # noqa: E402

TOL = 1e-9
CHECK_DAYS = 14          # сверка на хвосте обучения: test здесь не трогается


def _lr(x: np.ndarray, y) -> dict | None:
    """Та же логистическая регрессия, что в train.py; `None` — там она не строится."""
    from sklearn.linear_model import LogisticRegression              # noqa: PLC0415
    y = np.asarray(y, dtype=int)
    if y.min() == y.max():
        return None
    lr = LogisticRegression(solver="lbfgs", max_iter=1000 if x.shape[1] > 1 else 100)
    lr.fit(x, y)
    return {"coef": [float(c) for c in lr.coef_[0]], "intercept": float(lr.intercept_[0])}


def fit_bag(prepare, train, rows):
    """Мешок, голова и калибровка — повтор `train.run_fold` с сохранением параметров."""
    tr, _ = train.feature_engineering(rows, rows.iloc[:0], H.PRIMARY_H)
    feats = train.feature_names(tr)
    fit, es = prepare.es_split(tr, days=train.ES_DAYS, window_h=H.PRIMARY_H)
    boosters = train.train_model(fit, es, feats, train.sample_weights(fit), [])
    p_es = train.predict(boosters, es, feats)

    head = None
    m_fit = (fit["kind"] == "rearm").to_numpy()
    if H.PRIMARY_H >= train.REARM_HEAD_FROM_H and m_fit.sum() >= train.REARM_HEAD_MIN_ROWS:
        cols = list(train.REARM_HEAD_FEATS)
        head = _lr(np.log1p(fit.loc[m_fit, cols].to_numpy(dtype=float)), fit.loc[m_fit, "y"])
        if head is not None:
            head["features"] = cols
    p_es, _ = train.rearm_head(fit, es, es.iloc[:0], p_es, np.zeros(0), H.PRIMARY_H)
    platt = {}
    for kind in ("rearm", "tick"):
        m = (es["kind"] == kind).to_numpy()
        # как train.platt: один признак — логит скора
        platt[kind] = _lr(train._logit(p_es[m]).reshape(-1, 1), es.loc[m, "y"])
        if platt[kind] is not None:
            platt[kind] = {"coef": platt[kind]["coef"][0],
                           "intercept": platt[kind]["intercept"]}
    return boosters, feats, head, platt, len(tr)


def write(out: Path, boosters, feats, head, platt, n_rows: int, train) -> dict:
    from ml.serving import v3_bag                                     # noqa: PLC0415
    out.mkdir(parents=True)
    spec = []
    for seed, b in zip(H.SEEDS, boosters):
        name = f"booster_{seed}.txt"
        # num_iteration явно: по умолчанию save_model пишет только best_iteration деревьев,
        # а модель берёт не меньше MIN_TREES (train.n_trees) — хвост бустера терялся бы
        n = train.n_trees(b)
        b.save_model(str(out / name), num_iteration=n)
        spec.append({"file": name, "seed": seed, "n_trees": n})
    report = sorted(H.REPORT_DIR.glob("holdout_*.md"))[0]
    df = H.parse_report(report)
    main = df[df["основная строка"] == "да"].iloc[0]
    meta = {
        "model_version": f"lgbm-v3-bag-{date.today():%Y.%m.%d}",
        "model_format": v3_bag.FORMAT,
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feature_schema": "feat.v3",
        "feature_names": feats,
        "object_level": "pfx",
        "directions": ["sensor_failure"],
        "train_rows": n_rows,
        "holdout_precision": float(main["P"]),
        "holdout_recall": float(main["R"]),
        "holdout_median_lead_hours": float(main["median_lead_h"]),
        "holdout_report": str(report.relative_to(ROOT)),
        "alert_threshold": float(main["threshold"]),
        "horizon_h": H.PRIMARY_H,
        "policy": "одно открытое предупреждение на коллектор; срок жизни horizon_h; "
                  "новое — не раньше чем через час после старта инцидента",
        "train_code_sha256": H.LOOK_CODE,
        "boosters": spec,
        "rearm_head": head,
        "platt": platt,
    }
    meta["sha256"] = v3_bag.bag_sha256(out, [b["file"] for b in spec])
    (out / "model_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return meta


def check(out: Path, prepare, train, rows, inc) -> float:
    """Модель с диска против `run_fold` на последних сутках обучения."""
    from ml.serving import v3_bag                                     # noqa: PLC0415
    meta = json.loads((out / "model_meta.json").read_text())
    bag = v3_bag.Bag.load(out, meta)
    day = rows["d"].astype("datetime64[ns]")
    tail = rows[day > day.max() - np.timedelta64(CHECK_DAYS, "D")].reset_index(drop=True)
    want, _inc, _n, _t = train.run_fold((rows, tail, inc), time.time(), H.PRIMARY_H)
    _, va = train.feature_engineering(rows.iloc[:0], tail, H.PRIMARY_H)
    got = bag.predict_proba(va[meta["feature_names"]].to_numpy(dtype=float))
    return float(np.max(np.abs(got - want["p"].to_numpy())))


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else \
        ROOT / "models" / f"v3_{date.today():%Y%m%d}"
    if out.exists() and any(out.iterdir()):
        print(f"{out} уже есть и не пуст — не перезаписываю")
        return 1
    if out.exists():
        out.rmdir()
    changed = H.code_is_the_look_code()
    if changed:
        print(f"код не тот, которым сделан взгляд: {', '.join(changed)} — отказ")
        return 1
    prepare, train = H._setup([], H.FINAL_DATA)
    rows = H._load_rows(prepare, H.PRIMARY_H)
    inc = prepare.load_incidents()
    block = H.make_block(prepare, rows, inc, H.TEST_FROM, H.score_end(H.TEST_END, H.PRIMARY_H),
                         H.PRIMARY_H)
    boosters, feats, head, platt, n_rows = fit_bag(prepare, train, block["train"])
    meta = write(out, boosters, feats, head, platt, n_rows, train)
    err = check(out, prepare, train, block["train"], inc)
    print(f"{out}: {len(meta['boosters'])} бустеров, {len(feats)} признаков, "
          f"голова rearm {'есть' if head else 'нет'}, порог {meta['alert_threshold']}")
    print(f"сверка с run_fold на последних {CHECK_DAYS} сутках обучения: "
          f"наибольшее расхождение {err:.2e} — {'СОШЛОСЬ' if err <= TOL else 'РАЗОШЛОСЬ'}")
    return 0 if err <= TOL else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
