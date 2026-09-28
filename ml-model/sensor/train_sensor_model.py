"""Daily sensor detail: frozen LR + validation thresholds; retrospective replay.

Run from repository root (writes the model and evidence artifacts only on main):
    uv run --no-project --with numpy==2.3.3 --with pandas==2.3.3 \
      --with scikit-learn==1.7.2 python3 ml-model/sensor/train_sensor_model.py

Both real and synthetic modes are delivered. Real scores use only confirmed D5
history; synthetic scores use the forward simulator and have separate metrics.
Training ends before 2026; Q1 chooses horizon and thresholds; the chosen model is
NOT refitted. April-June is a retrospective test, already inspected in research.
Delivered scores and comparisons use the worker's daily 21:00 MSK snapshots.
Event-driven rearm is an explicitly separate research baseline, not runtime proof.
No one-open policy is silently applied to daily score rows.
"""

import bisect
import hashlib
import json
import math
import platform
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path[:0] = [str(ROOT / "code"), str(ROOT / "backend")]
from app.domain import failure_sim, sensor_risk
from predictive_metrics import evaluate_alerts

MSK = timezone(timedelta(hours=3))
HORIZONS = [12, 24, 36, 48]
DAY0, DAYN = date(2022, 4, 1), date(2026, 6, 30)
TRAIN = (date(2022, 4, 1), date(2026, 1, 1))
SELECT = (date(2026, 1, 1), date(2026, 4, 1))
TEST = (date(2026, 4, 1), date(2026, 7, 1))
NEG_SHARE = 0.1
CONFIRM = 3601  # integer seconds: D5 requires duration strictly greater than 1h
SEED = 263
EMPTY = np.array([], dtype="int64")
T0 = time.monotonic()


def load_data():
    """Read the immutable CSV snapshot explicitly; import has no training/IO effects."""
    global ch, act, C, cid, pos, kinds, KIND, f, F, GC, GP, COL, PK
    global TICKS, EPOCH_ORD, CHK, passports, checks_by
    ch = pd.read_csv(DATA / "channels.csv", dtype={"collector": "string"})
    act = ch[(ch.is_active == "t") & (ch.is_stub == "f")].reset_index(drop=True)
    C = len(act)
    assert C == 11_485, C
    cid = act.channel_id.to_numpy()
    pos = {int(c): i for i, c in enumerate(cid)}
    kinds = sorted(act.sensor_kind.dropna().unique())
    KIND = np.array([kinds.index(k) for k in act.sensor_kind])

    f = pd.read_csv(DATA / "failures.csv")
    f["t"] = pd.to_datetime(f.started_at, utc=True)
    f = f[f.t >= pd.Timestamp("2022-04-01", tz=MSK)]
    f = f.merge(ch[["channel_id", "object_id", "sensor_kind", "collector", "picket", "is_active", "is_stub"]],
                on="channel_id", how="left")
    w = pd.read_csv(DATA / "ppr_windows.csv")
    w = w[w.match == "sure"]
    ppr = np.zeros(len(f), bool)
    for r in w.itertuples():
        lo = pd.Timestamp(r.dismantle_from, tz=MSK)
        hi = pd.Timestamp(r.return_to, tz=MSK) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        ppr |= ((f.object_id == r.object_id) & (f.sensor_kind == r.sensor_kind) & (f.t >= lo) & (f.t <= hi)).to_numpy()
    log(f"реальных отказов с 2022-04-01: {len(f)}, из них в окнах ППР sure: {ppr.sum()}")
    f = f[~ppr].copy()
    f["s"] = f.t.astype("int64") // 10**9
    inactive = f[~f.channel_id.isin(cid)]
    log(f"отказов на неактивных каналах и заглушках (в метрики не входят): {len(inactive)}")







    F = by_channel(zip(f.channel_id, f.s))  # реальные отказы активных каналов, по индексу





    GC = groups([c if pd.notna(c) else None for c in f.collector])
    GP = groups([(c, int(p)) if pd.notna(c) and pd.notna(p) else None for c, p in zip(f.collector, f.picket)])
    COL = [c if pd.notna(c) else None for c in act.collector]
    PK = [(c, int(p)) if pd.notna(c) and pd.notna(p) else None for c, p in zip(act.collector, act.picket)]

    days = [DAY0 + timedelta(days=k) for k in range((DAYN - DAY0).days + 1)]
    TICKS = np.array([ts(d, 21) for d in days], dtype="int64")
    EPOCH_ORD = date(1970, 1, 1).toordinal()

    pp = pd.read_csv(DATA / "passports.csv").set_index("channel_id")
    chk = pd.read_csv(DATA / "checks.csv")
    chk["d"] = chk.measured_at.str[:10].map(date.fromisoformat)
    CHK = {int(c): (("motohours" if g.is_counter.iloc[0] == "t" else "calib"), sorted(g.d)) for c, g in chk.groupby("channel_id")}
    passports = {int(c): {"object_kind": r.object_kind, "in_service": date.fromisoformat(r.in_service_from),
                          "life": int(r.service_life_years)} for c, r in pp.iterrows()}
    checks_by = {c: v[1] for c, v in CHK.items()}

def log(*a):
    print(f"[{time.monotonic() - T0:6.0f} c]", *a, flush=True)


def ts(d, h=0):
    return int(datetime(d.year, d.month, d.day, h, tzinfo=MSK).timestamp())


def by_channel(pairs):
    g = {}
    for c, s in pairs:
        if int(c) in pos:
            g.setdefault(pos[int(c)], []).append(int(s))
    return {i: np.sort(np.array(v, dtype="int64")) for i, v in g.items()}


def groups(keys_of):
    """Отказы по группе (коллектор или пикет) — по всем каналам, и активным, и нет."""
    g = {}
    for k, s in zip(keys_of, f.s.to_numpy()):
        if k is not None:
            g.setdefault(k, []).append(int(s))
    return {k: np.sort(np.array(v)) for k, v in g.items()}


def simulate(pf, ratio):
    fails, pre = failure_sim.simulate(passports, checks_by, DAY0, DAYN, pf_days=pf, false_ratio=ratio)
    S = by_channel((c, t.timestamp()) for c, t in fails)
    P = by_channel((c, t.timestamp()) for c, t, _ in pre)
    return fails, pre, S, P


def moments(extra_of):
    """Моменты всех каналов: (канал, время, вид) по каналу и времени.
    вид 0 — tick 21:00, 1 — через час после своего отказа, 2 — после отказа соседа
    по пикету, 3 — через час после синтетического предвестника."""
    mc, mt, mk = [], [], []
    for i in range(C):
        parts = [(TICKS, 0)] + [(a, k) for a, k in extra_of(i)]
        t = np.concatenate([p for p, _ in parts])
        k = np.concatenate([np.full(len(p), kk, "int8") for p, kk in parts])
        o = np.lexsort((k, t))
        t, k = t[o], k[o]
        keep = np.ones(len(t), bool)
        keep[1:] = t[1:] != t[:-1]  # совпавшие моменты — один
        mc.append(np.full(keep.sum(), i, "int32"))
        mt.append(t[keep])
        mk.append(k[keep])
    return np.concatenate(mc), np.concatenate(mt), np.concatenate(mk)


def cnt(arr, lo_excl, hi_incl):
    return np.searchsorted(arr, hi_incl, "right") - np.searchsorted(arr, lo_excl, "right")


def real_features(mc, mt):
    X = np.zeros((len(mt), len(sensor_risk.REAL)), "float32")
    starts = np.searchsorted(mc, np.arange(C + 1))
    for i in range(C):
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        s = F.get(i, EMPTY)
        idx = np.searchsorted(s, t - CONFIRM, "right")
        last = s[np.maximum(idx - 1, 0)] if len(s) else np.zeros(len(t), "int64")
        dd = np.where(idx > 0, np.minimum((t - last) / 86400, sensor_risk.CAP), sensor_risk.CAP)
        n7, n30, n90 = (idx - np.searchsorted(s, t - k * 86400, "right") for k in (7, 30, 90))
        gc = cnt(GC.get(COL[i], EMPTY), t - 7 * 86400, t - CONFIRM) - n7 if COL[i] is not None else 0 * n7
        gp = cnt(GP.get(PK[i], EMPTY), t - 7 * 86400, t - CONFIRM) - n7 if PK[i] is not None else 0 * n7
        fresh = (dd * 24 < sensor_risk.FRESH_H).astype(float)
        X[a:b] = np.stack([fresh, np.exp(-dd), np.exp(-dd / 30), np.log1p(n7), np.log1p(n30), np.log1p(n90),
                           np.log1p(gp), np.log1p(gc)], axis=1)
    return X


def sim_features(mc, mt, P):
    X = np.zeros((len(mt), len(sensor_risk.SIM)), "float32")
    starts = np.searchsorted(mc, np.arange(C + 1))
    for i in range(C):
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        c = int(cid[i])
        p = passports[c]
        dord = (t + 3 * 3600) // 86400 + EPOCH_ORD  # московская дата момента
        age = np.maximum(dord - p["in_service"].toordinal(), 0)
        life = np.minimum(age / 365.25 / p["life"], 2.0)
        check = over = np.zeros(len(t))
        if c in CHK:
            kind, ds = CHK[c]
            iv = sensor_risk.INTERVAL[kind]
            o = np.array([d.toordinal() for d in ds])
            j = np.searchsorted(o, dord, "right")
            since = np.where(j > 0, dord - o[np.maximum(j - 1, 0)], age)
            check, over = np.minimum(since / iv, 3.0), (since > iv).astype(float)
        pr = P.get(i, EMPTY)
        # возраст предвестника в [lo, hi) сут ⇔ момент предвестника в (t − hi, t − lo]
        bins = [np.log1p(cnt(pr, t - hi * 86400, t - lo * 86400)) for lo, hi in sensor_risk.PRE_BINS]
        X[a:b] = np.stack([life, check, over, *bins], axis=1)
    return X


def labels(mc, mt, world, H):
    y = np.zeros(len(mt), bool)
    starts = np.searchsorted(mc, np.arange(C + 1))
    for i, s in world.items():
        a, b = starts[i], starts[i + 1]
        y[a:b] = cnt(s, mt[a:b], mt[a:b] + H * 3600) > 0
    return y


def onehot(mc):
    k = np.zeros((len(mc), len(kinds)), "float32")
    k[np.arange(len(mc)), KIND[mc]] = 1
    return k


class Scaled:
    """Логистическая регрессия на стандартизированных признаках; coef_ и intercept_ —
    в исходной шкале, как их читает sensor_risk._logit(). На исходной шкале lbfgs при
    доле положительных 0,02 % останавливался на 12-й итерации с невыученными
    коэффициентами (знак у доли выработки выходил обратным)."""

    def fit(self, X, y, w):
        mu, sd = X.mean(axis=0), X.std(axis=0)
        sd[sd == 0] = 1
        m = LogisticRegression(C=1.0, max_iter=20_000, tol=1e-10)
        m.fit((X - mu) / sd, y, sample_weight=w)
        self.n_iter = int(m.n_iter_[0])
        assert self.n_iter < 20_000, "регрессия не сошлась"
        self.coef_ = m.coef_[0] / sd
        self.intercept_ = float(m.intercept_[0] - (m.coef_[0] * mu / sd).sum())
        return self

    def proba(self, X):
        return 1 / (1 + np.exp(-(X.astype("float64") @ self.coef_ + self.intercept_)))


def window_mask(mt, period, H):
    """Моменты окна: с первого tick окна до последнего tick, у которого окно метки
    [tick, tick + H] не выходит за конец периода."""
    lo = ts(period[0], 21)
    last = ts(period[1]) - H * 3600
    hi = TICKS[TICKS <= last].max()
    return (mt >= lo) & (mt <= hi), lo, hi


class Design:
    def __init__(self, mc, mt, mk, X, mode):
        self.mc, self.mt, self.mk, self.X = mc, mt, mk, X
        self.mode = mode

    def rows(self, m):
        """Строки для регрессии. У модели журнала свежий отказ (fresh = 1) отключает
        остальные признаки и вид датчика: все такие моменты получают одну вероятность,
        и модель на них повторяет правило «канал отказал час назад»."""
        x = np.hstack([self.X[m], onehot(self.mc[m])])
        if self.mode == "real":
            fr = x[:, :1]
            x = np.hstack([fr, x[:, 1:] * (1 - fr)])
        return x


def fit(D, world, H, period):
    m, _, _ = window_mask(D.mt, period, H)
    y = labels(D.mc, D.mt, world, H)
    rng = np.random.default_rng(SEED + H + (100 if D.mode == "sim" else 0))
    keep = m & (y | (rng.random(len(y)) < NEG_SHARE))
    wts = np.where(y[keep], 1.0, 1 / NEG_SHARE)
    model = Scaled().fit(D.rows(keep), y[keep], wts)
    log(f"    H={H}: строк {m.sum():,}, положительных {y[m].sum():,}, обучено на {keep.sum():,}, итераций {model.n_iter}")
    return model


def failures_in(world, lo, hi, H):
    out = {}
    for i, a in world.items():
        v = a[(a > lo) & (a <= hi + H * 3600)]
        if len(v):
            out[i] = v
    return out


def fast(ai, at, fails, H):
    """evaluate_alerts(horizon_hours=0, max_lead_hours=H) по каналам через bisect;
    с оригиналом сверяется в exact()."""
    o = np.lexsort((at, ai))
    ai, at = ai[o], at[o]
    starts = np.searchsorted(ai, np.arange(C + 1))
    tp = fp = fn = 0
    for i in set(np.unique(ai).tolist()) | set(fails):
        al = at[starts[i]:starts[i + 1]].tolist()
        used, matched = [False] * len(al), [False] * len(al)
        for t in fails.get(i, EMPTY).tolist():
            a = bisect.bisect_left(al, t - H * 3600)
            b = bisect.bisect_left(al, t)  # labels require failure strictly after the score
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
    return {"tp": tp, "fp": fp, "fn": fn, "alerts": len(at), "precision": p, "recall": r}


def f1(m):
    p, r = m["precision"], m["recall"]
    return 2 * p * r / (p + r) if p + r else 0.0


def ceiling(D, m, fails, H):
    """Потолок Recall набора моментов: доля отказов, перед которыми в пределах H ч
    есть хоть один момент решения этого канала."""
    ok = n = 0
    starts = np.searchsorted(D.mc, np.arange(C + 1))
    for i, a in fails.items():
        t = D.mt[starts[i]:starts[i + 1]]
        t = t[m[starts[i]:starts[i + 1]]]
        for s in a.tolist():
            n += 1
            ok += cnt(t, s - H * 3600 - 1, s - 1).item() > 0
    return round(ok / n, 3) if n else 0.0, ok, n


def check_features(D, P=None):
    """Признаки скрипта против sensor_risk.features() на случайных моментах."""
    rng = np.random.default_rng(SEED)
    # Include cuts immediately after observed events; uniform sampling alone mostly
    # checks never-failing channels in this severely imbalanced archive.
    history = F if P is None else P
    starts = np.searchsorted(D.mc, np.arange(C + 1))
    event_rows = []
    for i, events in history.items():
        a, b = starts[i], starts[i + 1]
        j = np.searchsorted(D.mt[a:b], events + (CONFIRM if P is None else 0))
        event_rows.extend((a + j[j < b - a]).tolist())
    near = rng.choice(event_rows, min(len(event_rows), 300), replace=False) if event_rows else np.array([], dtype=int)
    idx = np.concatenate([near, rng.integers(0, len(D.mt), 700)]).astype(int)
    for r in idx:
        i, t = int(D.mc[r]), int(D.mt[r])
        at = datetime.fromtimestamp(t, MSK)
        starts = [datetime.fromtimestamp(s, MSK) for s in F.get(i, EMPTY).tolist()]
        if P is None:
            nb = (round(math.expm1(D.X[r, 6])), round(math.expm1(D.X[r, 7])))
            got = [sensor_risk.features(starts, at, nb=nb)[k] for k in sensor_risk.REAL]
            # соседей в лоб по таблице отказов
            g = f[(f.s <= t - CONFIRM) & (f.s > t - 7 * 86400)]
            n7 = sum(1 for s in F.get(i, EMPTY).tolist() if CONFIRM <= t - s < 7 * 86400)
            want = (int(((g.collector == COL[i]) & (g.picket == (PK[i][1] if PK[i] else -1))).sum()) - n7 if PK[i] else 0,
                    int((g.collector == COL[i]).sum()) - n7 if COL[i] is not None else 0)
            assert nb == want, (i, at, nb, want)
        else:
            c = int(cid[i])
            e = passports[c]
            eq = {"in_service": e["in_service"], "life": e["life"], "points": []}
            if c in CHK:
                eq["points"] = [{"kind": CHK[c][0], "readings": [(d,) for d in CHK[c][1]]}]
            pre = [datetime.fromtimestamp(s, MSK) for s in P.get(i, EMPTY).tolist()]
            x = sensor_risk.features([], at, eq, pre=pre)
            got = [x[k] for k in sensor_risk.SIM]
        assert np.allclose(got, D.X[r], atol=1e-5), (i, at, got, D.X[r].tolist())


def exact(ai, at, fails, H, min_lead_seconds=1):
    """Event metrics on strict future labels, including operational lead times."""
    by = {}
    for i, t in zip(ai.tolist(), at.tolist()):
        by.setdefault(i, ([], []))[0].append((i, datetime.fromtimestamp(t, MSK)))
    for i, arr in fails.items():
        for t in arr.tolist():
            by.setdefault(i, ([], []))[1].append((i, datetime.fromtimestamp(t, MSK)))
    tp = fp = fn = dup = 0
    leads = []
    for alerts, events in by.values():
        r = evaluate_alerts(alerts, events, horizon_hours=min_lead_seconds / 3600,
                            max_lead_hours=H)
        tp += r['tp']; fp += r['fp']; fn += r['fn']; dup += r['dup']
        leads.extend(r['lead_hours'])
    if min_lead_seconds == 1:
        quick = fast(ai, at, fails, H)
        assert (quick['tp'], quick['fp'], quick['fn']) == (tp, fp, fn)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        'tp': tp, 'fp': fp, 'fn': fn, 'dup': dup, 'alerts': len(at),
        'incidents': tp + fn, 'precision': precision, 'recall': recall,
        'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        'precision_per_score_row': tp / len(at) if len(at) else 0.0,
        'median_lead_hours': float(np.median(leads)) if leads else None,
        'min_lead_hours': min(leads) if leads else None,
        'lead_under_24h_share': sum(x < 24 for x in leads) / len(leads) if leads else None,
        'lead_hours': leads,
        'matching_window_hours': [min_lead_seconds / 3600, H],
    }


def separated_threshold(sorted_p, k):
    """Include the kth largest score's entire tie group; cut in the empty gap.

    A threshold equal to a score is unstable under float evaluation/serialization.
    No labels are consulted here. All-equal scores intentionally select all rows.
    """
    cutoff = sorted_p[-min(max(int(k), 1), len(sorted_p))]
    j = np.searchsorted(sorted_p, cutoff, side='left')
    if j == 0:
        return 0.0
    lower = sorted_p[j - 1]
    return float(lower + (cutoff - lower) / 2)


def curve(D, m, p, fails, H):
    order = np.sort(p)
    out = []
    for k in np.unique(np.geomspace(20, min(len(p), 400_000), 60).astype(int)):
        threshold = separated_threshold(order, k)
        selected = p >= threshold
        result = fast(D.mc[m][selected], D.mt[m][selected], fails, H)
        out.append({'threshold': threshold, **result, 'f1': f1(result)})
    return out


def choose(cv):
    high = max(cv, key=lambda r: (r['f1'], r['precision'], -r['alerts']))
    watch = min((r for r in cv if r['threshold'] <= high['threshold']),
                key=lambda r: abs(r['alerts'] - 10 * high['alerts']))
    return high['threshold'], watch['threshold']


def evaluate(D, m, p, fails, H, threshold, min_lead_seconds=1):
    selected = p >= threshold
    return exact(D.mc[m][selected], D.mt[m][selected], fails, H, min_lead_seconds)


def ranked(D, m, scores, world, fails, H, budgets=(5, 10, 20)):
    """Global fixed K per 21:00 snapshot; channel ID breaks score ties.

    Label-level precision/recall and event-level metrics are named separately.
    All methods get identical channel candidates, decision times and budgets.
    """
    mc, mt = D.mc[m], D.mt[m]
    order = np.lexsort((cid[mc], -scores, mt))
    _, counts = np.unique(mt[order], return_counts=True)
    within = np.arange(len(order)) - np.repeat(np.cumsum(counts) - counts, counts)
    y = labels(mc, mt, world, H)
    result = {}
    for k in budgets:
        selected = order[within < k]
        result[str(k)] = {
            'budget': 'global channels per daily snapshot', 'k': k,
            'snapshots': len(counts), 'selected_rows': len(selected),
            'positive_selected_rows': int(y[selected].sum()),
            'positive_candidate_rows': int(y.sum()),
            'precision_at_k': float(y[selected].mean()) if len(selected) else 0.0,
            'row_recall_at_k': float(y[selected].sum() / y.sum()) if y.sum() else 0.0,
            'event_metrics': exact(mc[selected], mt[selected], fails, H),
        }
    return result


def export(model, names, mode, high, watch):
    """JSON retains all float precision; no rounding before level decisions."""
    out = {'intercept': float(model.intercept_),
           'coef': {n: float(v) for n, v in zip(names, model.coef_)},
           'kind': {k: float(v) for k, v in zip(kinds, model.coef_[len(names):])},
           'thresholds': {'high': high, 'watch': watch}}
    if mode == 'real':
        out['gate'] = 'fresh'
    else:
        out.update(pf_days=failure_sim.PF_DAYS, false_ratio=failure_sim.FALSE_RATIO)
    return json.loads(json.dumps(out))


def exported_proba(model, D, m):
    names = sensor_risk.REAL if D.mode == 'real' else sensor_risk.SIM
    coef = np.array([model['coef'][n] for n in names] + [model['kind'][k] for k in kinds])
    return 1 / (1 + np.exp(-(D.rows(m).astype('float64') @ coef + model['intercept'])))


def verify_export(model, exported, D, m):
    expected, got = model.proba(D.rows(m)), exported_proba(exported, D, m)
    assert np.array_equal(expected, got), 'JSON changed probabilities'
    for threshold in exported['thresholds'].values():
        assert np.array_equal(expected >= threshold, got >= threshold), 'JSON changed decisions'
    rows = np.flatnonzero(m)
    rng = np.random.default_rng(SEED)
    sample = rng.choice(len(rows), min(len(rows), 1000), replace=False)
    for threshold in exported['thresholds'].values():
        sample = np.concatenate((sample, np.argsort(np.abs(got - threshold))[:30]))
    names = sensor_risk.REAL if D.mode == 'real' else sensor_risk.SIM
    for j in np.unique(sample):
        r = rows[j]
        x = dict(zip(names, D.X[r].astype(float)))
        z = sensor_risk._logit(exported, x, kinds[KIND[D.mc[r]]])
        runtime_p = 1 / (1 + math.exp(-z))
        assert np.isclose(runtime_p, got[j], rtol=1e-12, atol=1e-15)
        for threshold in exported['thresholds'].values():
            assert (runtime_p >= threshold) == (got[j] >= threshold), 'runtime level changed'
    return {'all_probability_rows': len(rows), 'all_decision_rows': len(rows),
            'scalar_runtime_rows': len(np.unique(sample)), 'passed': True}


def rearm_research(world, lo, hi, H):
    alerts_c, alerts_t = [], []
    for i, events in world.items():
        times = events + CONFIRM
        times = times[(times >= lo) & (times <= hi)]
        alerts_c.extend([i] * len(times)); alerts_t.extend(times.tolist())
    ai, at = np.array(alerts_c, dtype='int32'), np.array(alerts_t, dtype='int64')
    fails = failures_in(world, lo, hi, H)
    return {'policy': 'research only; each own failure confirmed after >1h; not worker runtime',
            'all_leads': exact(ai, at, fails, H),
            'lead_at_least_24h': exact(ai, at, fails, H, 24 * 3600) if H >= 24 else None}


def main():
    assert sensor_risk.CONFIRM_SECONDS == CONFIRM
    load_data()
    log('forward synthetic simulation; real outcomes are not read by generator')
    sim_fails, sim_pre, SIM, PRE = simulate(failure_sim.PF_DAYS, failure_sim.FALSE_RATIO)
    with (HERE / 'sim_failures.csv').open('w') as fh:
        fh.write('channel_id,started_at\n')
        for c, t in sim_fails:
            fh.write(f'{c},{t.isoformat(timespec="seconds")}\n')
    with (HERE / 'sim_failures_precursors.csv').open('w') as fh:
        fh.write('channel_id,observed_at,is_true\n')
        for c, t, ok in sim_pre:
            fh.write(f'{c},{t.isoformat(timespec="seconds")},{"t" if ok else "f"}\n')
    log(f'simulated failures {len(sim_fails)}, precursors {len(sim_pre)}')
    mc, mt, mk = moments(lambda i: [])
    designs = {'real': Design(mc, mt, mk, real_features(mc, mt), 'real'),
               'sim': Design(mc, mt, mk, sim_features(mc, mt, PRE), 'sim')}
    worlds = {'real': F, 'sim': SIM}
    for name, D in designs.items():
        check_features(D, PRE if name == 'sim' else None)
    source_paths = [Path(__file__).resolve(), ROOT / 'backend/app/domain/sensor_risk.py',
                    ROOT / 'backend/app/domain/failure_sim.py', ROOT / 'code/predictive_metrics.py']
    metadata = {
        'training_policy': 'freeze train-through-2025 coefficients and Q1 thresholds; no refit or carry',
        'decision_schedule': 'daily 21:00 Europe/Moscow', 'alert_policy': 'each daily high score row',
        'test_status': 'retrospective, April-June already examined in preceding research',
        'target': 'next D5 channel episode (not confirmed physical sensor breakage), sure PPR excluded',
        'min_failure_duration_seconds_strict': 3600, 'known_at_delay_seconds': CONFIRM,
        'train_period': [str(x) for x in TRAIN], 'select_period': [str(x) for x in SELECT],
        'test_period': [str(x) for x in TEST], 'seed': SEED,
        'negative_sampling': {'share': NEG_SHARE, 'weight': 1 / NEG_SHARE},
        'ranking_budget': 'global K=5/10/20 per daily snapshot, stable channel-ID tie break',
        'limits': ['current active non-stub catalog reused historically; no historical exposure windows',
                   'D5 labels contain shared-infrastructure failures; no repair-confirmed sensor gold',
                   'synthetic performance is simulation-only and cannot establish real-world quality'],
        'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                        'pandas': pd.__version__, 'scikit_learn': sklearn.__version__},
        'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(DATA.glob('*.csv'))},
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in source_paths},
    }
    run = {'metadata': metadata, 'horizons': {}, 'chosen': None, 'validation': {}, 'test': {}}
    models = {}
    for H in HORIZONS:
        run['horizons'][H] = {}
        for name, D in designs.items():
            world = worlds[name]
            m, lo, hi = window_mask(D.mt, SELECT, H)
            fails = failures_in(world, lo, hi, H)
            model = fit(D, world, H, TRAIN)
            models[(name, H, 'tick')] = model
            p = model.proba(D.rows(m))
            cv = curve(D, m, p, fails, H)
            high, watch = choose(cv)
            result = {'high': high, 'watch': watch, 'model': evaluate(D, m, p, fails, H, high),
                      'curve': cv, 'ceiling_tick': ceiling(D, m, fails, H)}
            run['horizons'][H][name] = result
            log(f'validation {name} H={H}: P={result["model"]["precision"]:.4f}, R={result["model"]["recall"]:.4f}')
    H = max(HORIZONS, key=lambda h: (run['horizons'][h]['real']['model']['f1'], -h))
    run['chosen'] = {'horizon': H, 'moments': 'tick', 'selection': 'real validation event F1; shorter H breaks ties'}
    artifact = {'version': f'sensor-lr-h{H}-tick-frozen-2026.09.28', 'horizon_h': H,
                'moments': 'tick', 'trained': [str(TRAIN[0]), str(TRAIN[1] - timedelta(days=1))],
                'training_policy': metadata['training_policy'], 'decision_schedule': metadata['decision_schedule'],
                'alert_policy': metadata['alert_policy'], 'modes': {}}
    for name, D in designs.items():
        sel = run['horizons'][H][name]
        model = models[(name, H, 'tick')]
        names = sensor_risk.REAL if name == 'real' else sensor_risk.SIM
        artifact['modes'][name] = export(model, names, name, sel['high'], sel['watch'])
        for period_name, period in (('validation', SELECT), ('test', TEST)):
            m, lo, hi = window_mask(D.mt, period, H)
            fails = failures_in(worlds[name], lo, hi, H)
            parity = verify_export(model, artifact['modes'][name], D, m)
            p = exported_proba(artifact['modes'][name], D, m)
            result = {'cuts': [datetime.fromtimestamp(t, MSK).isoformat() for t in (lo, hi)],
                      'export_parity': parity,
                      'model': evaluate(D, m, p, fails, H, sel['high']),
                      'model_watch': evaluate(D, m, p, fails, H, sel['watch']),
                      'model_lead_at_least_24h': evaluate(D, m, p, fails, H, sel['high'], 86400) if H >= 24 else None,
                      'ranking_model': ranked(D, m, p, worlds[name], fails, H),
                      'ceiling_tick': ceiling(D, m, fails, H)}
            if name == 'real':
                recency = D.X[m, 1]  # monotone exp(-days since last confirmed start)
                recent = recency >= math.exp(-1)
                result['rule_tick_1d'] = exact(D.mc[m][recent], D.mt[m][recent], fails, H)
                result['ranking_recency'] = ranked(D, m, recency, F, fails, H)
                result['research_rearm'] = rearm_research(F, lo, hi, H)
            else:
                # Same candidate/time/budget baseline; counts of observed synthetic precursors only.
                precursor = D.X[m, 3:].sum(axis=1)
                result['ranking_precursor'] = ranked(D, m, precursor, SIM, fails, H)
            run[period_name][name] = result
            log(f'{period_name} {name}, selected H={H}: {result["model"]["tp"]} TP, {result["model"]["fp"]} FP, {result["model"]["fn"]} FN')
    model_path = ROOT / 'backend/app/domain/sensor_model.json'
    model_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1, allow_nan=False) + '\n')
    run['model_sha256'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    (HERE / 'run.json').write_text(json.dumps(run, ensure_ascii=False, indent=1, allow_nan=False) + '\n')
    # Hash only generated artifacts and inputs; do not claim old narrative files match this run.
    generated = [model_path, HERE / 'run.json', HERE / 'sim_failures.csv', HERE / 'sim_failures_precursors.csv']
    (HERE / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n' for p in generated))
    (model_path.parent / 'SHA256SUMS').write_text(f'{run["model_sha256"]}  sensor_model.json\n')
    log('complete: both mandatory modes exported, retrospective replay and hashes written')


if __name__ == '__main__':
    main()
