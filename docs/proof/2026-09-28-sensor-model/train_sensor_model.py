"""Модель до датчика: обучение, выбор горизонта и порога, замер. SL.10 (MOS-263).

Повтор из корня репозитория (около 10 минут на 4 ядрах):

    python3 code/failure_sim.py --data docs/proof/2026-09-28-sensor-model/data \\
        --out docs/proof/2026-09-28-sensor-model/sim_failures.csv
    uv run --no-project --with numpy==2.3.3 --with pandas==2.3.3 \\
        --with scikit-learn==1.7.2 \\
        python3 docs/proof/2026-09-28-sensor-model/train_sensor_model.py

Порядок (дополнение 2 к MOS-263):
  1. Строка — «канал × сутки», срез 21:00 МСК, 11 485 активных каналов.
     Метка горизонта N — отказ канала в (срез, срез + N ч], N ∈ {12, 24, 36, 48}.
  2. Учим на срезах 2022-04-01…2025-12-31, смотрим Precision/Recall на срезах
     2026-01-01…2026-03-31 (окно выбора). Там же выбираем горизонт, порог уровня
     high (лучший F1) и watch (вдесятеро больше предупреждений, чем у high), и N суток у правила недавности.
  3. Один раз переучиваем выбранный горизонт на 2022-04-01…2026-03-31 и считаем
     числа на 2026-04-01…2026-06-30. По этому окну ничего не выбираем.
  Режима два, архитектура одна (логистическая регрессия): real — реальные признаки
  и реальные отказы; synthetic — плюс синтетический паспорт, к отказам добавлены
  симулированные code/failure_sim.py («симулированный мир»).
  Метрики — evaluate_alerts() из code/predictive_metrics.py, объект — канал,
  окно зачёта [0, N] ч.

Пишет: backend/app/domain/sensor_model.json (коэффициенты выбранного варианта)
и docs/proof/2026-09-28-sensor-model/run.json (все числа для metrics.md).
"""

import bisect
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path[:0] = [str(ROOT / "code"), str(ROOT / "backend")]
from app.domain import sensor_risk
from predictive_metrics import evaluate_alerts

MSK = timezone(timedelta(hours=3))
HORIZONS = [12, 24, 36, 48]
DAY0, DAYN = date(2022, 4, 1), date(2026, 6, 30)
TRAIN = (date(2022, 4, 1), date(2026, 1, 1))  # срезы до этой даты, метка внутри
SELECT = (date(2026, 1, 1), date(2026, 4, 1))
REFIT = (date(2022, 4, 1), date(2026, 4, 1))
TEST = (date(2026, 4, 1), date(2026, 7, 1))
NEG_SHARE = 0.1  # доля отрицательных строк в обучении, вес 1/доля
RECENCY = [1, 2, 3, 7, 14, 30]
T0 = time.monotonic()


def log(*a):
    print(f"[{time.monotonic() - T0:6.0f} c]", *a, flush=True)


def sec(ts):
    return ts.astype("int64") // 10**9


# ------------------------------------------------------------------ данные
ch = pd.read_csv(DATA / "channels.csv", dtype={"collector": "string"})
act = ch[(ch.is_active == "t") & (ch.is_stub == "f")].reset_index(drop=True)
C = len(act)
assert C == 11_485, C
cid = act.channel_id.to_numpy()
pos = {c: i for i, c in enumerate(cid)}
kinds = sorted(act.sensor_kind.dropna().unique())

f = pd.read_csv(DATA / "failures.csv")
f["t"] = pd.to_datetime(f.started_at, utc=True)
f = f[f.t >= pd.Timestamp("2022-04-01", tz=MSK)]
# окна ППР match = sure: эпизод газового датчика узла в окне — не отказ
w = pd.read_csv(DATA / "ppr_windows.csv")
w = w[w.match == "sure"]
f = f.merge(
    ch[["channel_id", "object_id", "sensor_kind", "collector", "picket"]],
    on="channel_id",
    how="left",
)
ppr = np.zeros(len(f), bool)
for r in w.itertuples():
    lo = pd.Timestamp(r.dismantle_from, tz=MSK)
    hi = pd.Timestamp(r.return_to, tz=MSK) + pd.Timedelta(
        hours=23, minutes=59, seconds=59
    )
    ppr |= (
        (f.object_id == r.object_id)
        & (f.sensor_kind == r.sensor_kind)
        & (f.t >= lo)
        & (f.t <= hi)
    ).to_numpy()
log(f"реальных отказов с 2022-04-01: {len(f)}, из них в окнах ППР sure: {ppr.sum()}")
f = f[~ppr]
f["s"] = sec(f.t)

sim = pd.read_csv(HERE / "sim_failures.csv")
sim["s"] = sec(pd.to_datetime(sim.started_at, utc=True))
log(f"симулированных отказов: {len(sim)}")

# сетка срезов: сутки d, срез d 21:00 МСК
days = pd.date_range(DAY0, DAYN, freq="D")
D = len(days)
cuts = np.array(
    [int(datetime(d.year, d.month, d.day, 21, tzinfo=MSK).timestamp()) for d in days]
)
dates = np.array([d.date() for d in days])


def didx(d):
    return int(np.searchsorted(dates, d))


# ------------------------------------------------------------------ признаки
def by_channel(df):
    g = {}
    for c, s in zip(df.channel_id.to_numpy(), df.s.to_numpy()):
        g.setdefault(c, []).append(s)
    return {c: np.sort(np.array(v)) for c, v in g.items()}


F = by_channel(f)
SIM = by_channel(sim)


def window_count(arr, lo_excl, hi_incl):
    return np.searchsorted(arr, hi_incl, "right") - np.searchsorted(
        arr, lo_excl, "right"
    )


def group_count(keycol):
    """Отказы группы (коллектор или пикет) за 7 сут до среза, по всем каналам группы."""
    g = {}
    ff = f.dropna(subset=["collector"])
    if keycol == "picket":
        ff = ff.dropna(subset=["picket"])
        keys = list(zip(ff.collector, ff.picket.astype(int)))
    else:
        keys = list(ff.collector)
    for k, s in zip(keys, ff.s.to_numpy()):
        g.setdefault(k, []).append(s)
    return {k: np.sort(np.array(v)) for k, v in g.items()}


GC, GP = group_count("collector"), group_count("picket")
EMPTY = np.array([], dtype="int64")

X = np.zeros(
    (C, D, 10), dtype="float32"
)  # r1 r30 n7 n30 n90 nb_pk nb_col life check overdue
pp = pd.read_csv(DATA / "passports.csv").set_index("channel_id")
chk = pd.read_csv(DATA / "checks.csv")
chk["d"] = chk.measured_at.str[:10].map(date.fromisoformat)
CHK = {
    c: (("motohours" if g.is_counter.iloc[0] == "t" else "calib"), sorted(g.d))
    for c, g in chk.groupby("channel_id")
}
day_ord = np.array([d.toordinal() for d in dates])

for i, c in enumerate(cid):
    a = F.get(c, EMPTY)
    idx = np.searchsorted(a, cuts, "right")
    last = np.where(idx > 0, a[np.maximum(idx - 1, 0)] if len(a) else 0, 0)
    dd = np.where(idx > 0, np.minimum((cuts - last) / 86400, 365), 365)
    n7 = idx - np.searchsorted(a, cuts - 7 * 86400, "right")
    n30 = idx - np.searchsorted(a, cuts - 30 * 86400, "right")
    n90 = idx - np.searchsorted(a, cuts - 90 * 86400, "right")
    col = act.collector.iloc[i]
    pk = act.picket.iloc[i]
    gc = (
        window_count(GC.get(col, EMPTY), cuts - 7 * 86400, cuts) - n7
        if pd.notna(col)
        else 0 * n7
    )
    gp = (
        window_count(GP.get((col, int(pk)), EMPTY), cuts - 7 * 86400, cuts) - n7
        if pd.notna(col) and pd.notna(pk)
        else 0 * n7
    )
    X[i, :, 0] = np.exp(-dd)
    X[i, :, 1] = np.exp(-dd / 30)
    X[i, :, 2] = np.log1p(n7)
    X[i, :, 3] = np.log1p(n30)
    X[i, :, 4] = np.log1p(n90)
    X[i, :, 5] = np.log1p(gp)
    X[i, :, 6] = np.log1p(gc)
    p = pp.loc[c]
    age = np.maximum(day_ord - date.fromisoformat(p.in_service_from).toordinal(), 0)
    X[i, :, 7] = np.minimum(age / 365.25 / p.service_life_years, 2.0)
    if c in CHK:
        kind, ds = CHK[c]
        iv = sensor_risk.INTERVAL[kind]
        o = np.array([d.toordinal() for d in ds])
        j = np.searchsorted(o, day_ord, "right")
        since = np.where(j > 0, day_ord - o[np.maximum(j - 1, 0)], age)
        X[i, :, 8] = np.minimum(since / iv, 3.0)
        X[i, :, 9] = since > iv
KIND = np.array([kinds.index(k) if isinstance(k, str) else -1 for k in act.sensor_kind])
log("признаки готовы", X.shape)


# сверка с единственной реализацией тика: sensor_risk.features() на случайных строках
def eq_of(c):
    p = pp.loc[c]
    e = {
        "in_service": date.fromisoformat(p.in_service_from),
        "life": int(p.service_life_years),
        "points": [],
    }
    if c in CHK:
        e["points"] = [{"kind": CHK[c][0], "readings": [(d,) for d in CHK[c][1]]}]
    return e


rng = np.random.default_rng(263)
hot = [pos[c] for c in F if c in pos]
for i, j in list(zip(rng.choice(hot, 1500), rng.integers(0, D, 1500))) + list(
    zip(rng.integers(0, C, 500), rng.integers(0, D, 500))
):
    c = cid[i]
    at = datetime.fromtimestamp(int(cuts[j]), MSK)
    starts = [datetime.fromtimestamp(int(s), MSK) for s in F.get(c, EMPTY)]
    col, pk = act.collector.iloc[i], act.picket.iloc[i]
    n7 = sum(1 for s in F.get(c, EMPTY) if 0 <= cuts[j] - s < 7 * 86400)
    nb = (
        int(np.expm1(X[i, j, 5]).round()),
        int(np.expm1(X[i, j, 6]).round()),
    )
    # соседей считаем здесь ещё раз, в лоб, по всем отказам группы
    grp = f[(f.collector == col) & (f.s <= cuts[j]) & (f.s > cuts[j] - 7 * 86400)]
    want_nb = (
        int(
            ((grp.picket == pk).sum() if pd.notna(pk) else 0)
            - (n7 if pd.notna(pk) else 0)
        ),
        int(len(grp) - n7) if pd.notna(col) else 0,
    )
    assert nb == want_nb, (c, at, nb, want_nb)
    x = sensor_risk.features(starts, at, eq_of(c), nb)
    got = [x[k] for k in sensor_risk.REAL + sensor_risk.SYNTH]
    assert np.allclose(got, X[i, j], atol=1e-5), (c, at, got, X[i, j].tolist())
log("признаки совпали с sensor_risk.features() на 2 000 строках")


# ------------------------------------------------------------------ метки
def labels(world, H):
    Y = np.zeros((C, D), bool)
    for i, c in enumerate(cid):
        a = world.get(c)
        if a is not None:
            Y[i] = window_count(a, cuts, cuts + H * 3600) > 0
    return Y


def merged(a, b):
    out = dict(a)
    for c, v in b.items():
        out[c] = np.sort(np.concatenate([out.get(c, EMPTY), v]))
    return out


WORLD = {"real": F, "synthetic": merged(F, SIM)}
COLS = {"real": list(range(7)), "synthetic": list(range(10))}
NAMES = sensor_risk.REAL + sensor_risk.SYNTH


def span(period, H):
    """Срезы периода, у которых окно метки [срез, срез + H] не выходит за конец периода."""
    j0 = didx(period[0])
    end = int(
        datetime(period[1].year, period[1].month, period[1].day, tzinfo=MSK).timestamp()
    )
    j1 = int(np.searchsorted(cuts, end - H * 3600, "right"))
    return j0, j1


def design(mode, j0, j1):
    x = X[:, j0:j1][:, :, COLS[mode]].reshape(-1, len(COLS[mode]))
    k = np.zeros((C, len(kinds)), "float32")
    k[np.arange(C), KIND] = 1
    k = np.repeat(k, j1 - j0, axis=0)
    return np.hstack([x, k])


def fit(mode, H, period):
    j0, j1 = span(period, H)
    Y = labels(WORLD[mode], H)[:, j0:j1].reshape(-1)
    Xd = design(mode, j0, j1)
    keep = Y | (rng.random(len(Y)) < NEG_SHARE)
    wts = np.where(Y[keep], 1.0, 1 / NEG_SHARE)
    # Признаки стандартизируем: на исходной шкале lbfgs при доле положительных 0,02 %
    # останавливался на 12-й итерации с невыученными коэффициентами (знак у доли
    # выработки выходил обратным). Коэффициенты Scaled пересчитывает обратно.
    m = Scaled().fit(Xd[keep], Y[keep], wts)
    log(
        f"  {mode} H={H}: строк {len(Y):,}, положительных {Y.sum():,}, "
        f"обучено на {keep.sum():,}, итераций {m.n_iter}"
    )
    return m


class Scaled:
    """Логистическая регрессия на стандартизированных признаках; coef_ и intercept_ —
    в исходной шкале, как их читает sensor_risk._logit()."""

    def fit(self, X, y, w):
        mu, sd = X.mean(axis=0), X.std(axis=0)
        sd[sd == 0] = 1
        m = LogisticRegression(C=1.0, max_iter=20_000, tol=1e-10)
        m.fit((X - mu) / sd, y, sample_weight=w)
        self.n_iter = int(m.n_iter_[0])
        assert self.n_iter < 20_000, "регрессия не сошлась"
        self.coef_ = (m.coef_[0] / sd)[None, :]
        self.intercept_ = np.array(
            [m.intercept_[0] - float((m.coef_[0] * mu / sd).sum())]
        )
        return self

    def predict_proba(self, X):
        z = X.astype("float64") @ self.coef_[0] + self.intercept_[0]
        p = 1 / (1 + np.exp(-z))
        return np.stack([1 - p, p], axis=1)


def predict(m, mode, j0, j1):
    return m.predict_proba(design(mode, j0, j1))[:, 1].reshape(C, j1 - j0)


# ------------------------------------------------------------------ замер
def failures_in(mode, j0, j1, H):
    """Отказы, которые окно срезов [j0, j1) вообще может поймать: (первый срез, последний + H]."""
    lo, hi = cuts[j0], cuts[j1 - 1] + H * 3600
    out = {}
    for c, a in WORLD[mode].items():
        if c in pos:
            v = a[(a > lo) & (a <= hi)]
            if len(v):
                out[c] = v
    return out


def fast(alert_mask, j0, fails, H):
    """То же, что evaluate_alerts(horizon_hours=0, max_lead_hours=H) по каналам,
    только через bisect; с оригиналом сверяется assert в exact()."""
    tp = fp = fn = 0
    rows = {i for i in np.flatnonzero(alert_mask.any(axis=1))} | {pos[c] for c in fails}
    for i in rows:
        al = cuts[j0 + np.flatnonzero(alert_mask[i])].tolist()
        used = [False] * len(al)
        matched = [False] * len(al)
        for t in fails.get(cid[i], EMPTY).tolist():
            a = bisect.bisect_left(al, t - H * 3600)
            b = bisect.bisect_right(al, t)
            best = None
            for k in range(b - 1, a - 1, -1):
                matched[k] = True
                if best is None and not used[k]:
                    best = k
            if best is None:
                fn += 1
            else:
                used[best] = True
                tp += 1
        fp += matched.count(False)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "alerts": int(alert_mask.sum()),
        "precision": p,
        "recall": r,
    }


def exact(alert_mask, j0, fails, H):
    """Числа в отчёт: evaluate_alerts() как есть, объект — канал."""
    al = [
        (int(cid[i]), datetime.fromtimestamp(int(cuts[j0 + j]), MSK))
        for i, j in zip(*np.nonzero(alert_mask))
    ]
    fl = [
        (int(c), datetime.fromtimestamp(int(t), MSK))
        for c, a in fails.items()
        for t in a
    ]
    by = {}
    for o, t in al:
        by.setdefault(o, ([], []))[0].append((o, t))
    for o, t in fl:
        by.setdefault(o, ([], []))[1].append((o, t))
    tp = fp = fn = dup = 0
    for a, b in by.values():
        m = evaluate_alerts(a, b, horizon_hours=0, max_lead_hours=H)
        tp, fp, fn, dup = tp + m["tp"], fp + m["fp"], fn + m["fn"], dup + m["dup"]
    got = fast(alert_mask, j0, fails, H)
    assert (got["tp"], got["fp"], got["fn"]) == (tp, fp, fn), (got, tp, fp, fn)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "dup": dup,
        "alerts": len(al),
        "incidents": len(fl),
        "precision": round(p, 3),
        "recall": round(r, 3),
    }


def f_beta(m, b):
    p, r = m["precision"], m["recall"]
    return (1 + b * b) * p * r / (b * b * p + r) if p + r else 0.0


def curve(P, j0, fails, H):
    """Пороги — по числу предупреждений: верхние k строк окна, k по геометрической сетке."""
    flat = np.sort(P.reshape(-1))[::-1]
    out = []
    for k in np.unique(np.geomspace(20, min(len(flat), 400_000), 60).astype(int)):
        t = float(flat[k - 1])
        m = fast(P >= t, j0, fails, H)
        out.append({"threshold": t, **m, "f1": f_beta(m, 1), "f2": f_beta(m, 2)})
    return out


def recency_mask(j0, j1, n):
    """Правило недавности: предупреждение на срезе, если у канала был отказ за n сут."""
    idx = np.stack(
        [np.searchsorted(F.get(c, EMPTY), cuts[j0:j1], "right") for c in cid]
    )
    ago = np.stack(
        [
            np.searchsorted(F.get(c, EMPTY), cuts[j0:j1] - n * 86400, "right")
            for c in cid
        ]
    )
    return idx > ago


run = {"horizons": {}, "sim_failures": len(sim)}
log("выбор: учим на 2022-04-01…2025-12-31, смотрим 2026-01-01…2026-03-31")
models = {}
for H in HORIZONS:
    run["horizons"][H] = {}
    j0, j1 = span(SELECT, H)
    for mode in ("real", "synthetic"):
        m = fit(mode, H, TRAIN)
        models[(mode, H)] = m
        P = predict(m, mode, j0, j1)
        fails = failures_in(mode, j0, j1, H)
        cv = curve(P, j0, fails, H)
        hi = max(cv, key=lambda x: x["f1"])
        # watch — список наблюдения вдесятеро длиннее списка high на том же окне
        wa = min(cv, key=lambda x: abs(x["alerts"] - 10 * hi["alerts"]))
        rec = []
        for n in RECENCY:
            r = fast(recency_mask(j0, j1, n), j0, fails, H)
            rec.append({"days": n, **r, "f1": f_beta(r, 1)})
        rb = max(rec, key=lambda x: x["f1"])
        run["horizons"][H][mode] = {
            "high": hi["threshold"],
            "watch": wa["threshold"],
            "model": exact(P >= hi["threshold"], j0, fails, H),
            "model_watch": exact(P >= wa["threshold"], j0, fails, H),
            "recency_days": rb["days"],
            "recency": exact(recency_mask(j0, j1, rb["days"]), j0, fails, H),
            "curve": cv,
            "recency_curve": rec,
            "cuts": [str(dates[j0]), str(dates[j1 - 1])],
        }
        s = run["horizons"][H][mode]
        log(
            f"  {mode} H={H}: модель {s['model']}, недавность {s['recency_days']} сут {s['recency']}"
        )

# горизонт выбираем по лучшему F1 модели без синтетики на окне выбора
Hbest = max(HORIZONS, key=lambda H: f_beta(run["horizons"][H]["real"]["model"], 1))
run["chosen"] = Hbest
log(
    f"выбран горизонт {Hbest} ч; переучиваем на 2022-04-01…2026-03-31 и меряем апрель–июнь"
)

j0, j1 = span(TEST, Hbest)
out = {
    "version": f"sensor-lr-h{Hbest}-2026.09.28",
    "horizon_h": Hbest,
    "trained": [str(REFIT[0]), str(REFIT[1] - timedelta(days=1))],
    "modes": {},
}
run["test"] = {"cuts": [str(dates[j0]), str(dates[j1 - 1])]}
for mode in ("real", "synthetic"):
    sel = run["horizons"][Hbest][mode]
    m = fit(mode, Hbest, REFIT)
    P = predict(m, mode, j0, j1)
    fails = failures_in(mode, j0, j1, Hbest)
    run["test"][mode] = {
        "model": exact(P >= sel["high"], j0, fails, Hbest),
        "model_watch": exact(P >= sel["watch"], j0, fails, Hbest),
        "recency": exact(recency_mask(j0, j1, sel["recency_days"]), j0, fails, Hbest),
    }
    log(f"  тест {mode}: {run['test'][mode]}")
    names = [NAMES[k] for k in COLS[mode]]
    coef = m.coef_[0]
    out["modes"][mode] = {
        "intercept": float(m.intercept_[0]),
        "coef": {n: round(float(v), 6) for n, v in zip(names, coef)},
        "kind": {k: round(float(v), 6) for k, v in zip(kinds, coef[len(names) :])},
        "baseline": {
            "r1": 0.0,
            "r30": 0.0,
            "n7": 0.0,
            "n30": 0.0,
            "n90": 0.0,
            "nb_picket": 0.0,
            "nb_coll": 0.0,
            "life": 0.0,
            "check": 0.0,
            "overdue": 0.0,
        },
        "thresholds": {"high": round(sel["high"], 6), "watch": round(sel["watch"], 6)},
    }
    # экспорт сверяем с предсказанием sklearn через sensor_risk на тех же строках
    Xd = design(mode, j0, j1)
    for r in rng.integers(0, len(Xd), 300):
        x = dict(zip(names, Xd[r, : len(names)].astype(float)))
        k = kinds[int(np.argmax(Xd[r, len(names) :]))]
        z = sensor_risk._logit(out["modes"][mode], x, k)
        assert abs(1 / (1 + np.exp(-z)) - P.reshape(-1)[r]) < 1e-4
(ROOT / "backend/app/domain/sensor_model.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1) + "\n"
)
(HERE / "run.json").write_text(
    json.dumps(run, ensure_ascii=False, indent=1, default=float) + "\n"
)
log("готово: backend/app/domain/sensor_model.json, run.json")
