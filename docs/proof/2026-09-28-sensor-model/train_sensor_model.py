"""Модель до датчика: обучение, выбор горизонта и порога, замер. SL.10 (MOS-263).

Повтор из корня репозитория (около 15 минут на 4 ядрах; симуляция считается внутри):

    uv run --no-project --with numpy==2.3.3 --with pandas==2.3.3 \\
        --with scikit-learn==1.7.2 \\
        python3 docs/proof/2026-09-28-sensor-model/train_sensor_model.py

Моменты решения (доработка 28.09 вечер, п. 1). Как у модели коллектора v3
(ml-model/src/ml/moments.py), моментов два вида:
  tick  — 21:00 МСК каждых суток;
  rearm — через 1 ч после начала реального отказа канала: раньше система не знает,
          что эпизод станет отказом (отказ — эпизод дольше часа);
  rearm соседа — через 1 ч после отказа другого канала того же пикета; берётся,
          только если на окне выбора с ним лучше (решает скрипт, см. run.json).
Выдача — «одно открытое предупреждение», как у v3: предупреждение по каналу открыто
H часов или до первого отказа канала, и пока оно открыто, новое не выдаётся (one_open()).
Свежий отказ (fresh — начался меньше 2 ч назад) у модели журнала отключает остальные
признаки: на моментах rearm модель повторяет правило «канал отказал час назад».
Признаки считаются на момент решения. Метка горизонта N — отказ канала в (t, t + N ч].

Модели две, обе — логистическая регрессия:
  real — признаки журнала, реальные отказы; это режим «без синтетики»;
  sim  — признаки синтетического паспорта и синтетического предвестника, метка —
         только симулированные отказы (app.domain.failure_sim), моменты — tick и
         через 1 ч после предвестника. Метрики — отдельно, на симулированных отказах.
Режим «с синтетикой» на экране: 1 − (1 − p_real)(1 − p_sim).

Порядок (дополнение 2 к MOS-263): учим на 2022-04-01…2025-12-31, выбираем горизонт
(12/24/36/48 ч), порог high (лучший F1) и watch (вдесятеро больше предупреждений)
на 2026-01-01…2026-03-31, один раз переучиваем выбранное на 2022-04-01…2026-03-31
и считаем числа на 2026-04-01…2026-06-30. Метрики — evaluate_alerts() из
code/predictive_metrics.py, объект — канал, окно зачёта [0, N] ч.

Пишет backend/app/domain/sensor_model.json, docs/proof/2026-09-28-sensor-model/run.json,
sim_failures.csv и sim_failures_precursors.csv.
"""

import bisect
import json
import math
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
from app.domain import failure_sim, sensor_risk
from predictive_metrics import evaluate_alerts

MSK = timezone(timedelta(hours=3))
HORIZONS = [12, 24, 36, 48]
DAY0, DAYN = date(2022, 4, 1), date(2026, 6, 30)
TRAIN = (date(2022, 4, 1), date(2026, 1, 1))  # [начало, конец) окна
SELECT = (date(2026, 1, 1), date(2026, 4, 1))
REFIT = (date(2022, 4, 1), date(2026, 4, 1))
TEST = (date(2026, 4, 1), date(2026, 7, 1))
NEG_SHARE = 0.1  # доля отрицательных строк-tick в обучении, вес 1/доля
CONFIRM = 3600  # rearm через час после начала отказа
SENS = [(1.0, 1.0), (4.0, 1.0), (2.0, 0.5), (2.0, 3.0)]  # чувствительность (P-F, ложных)
T0 = time.monotonic()
rng = np.random.default_rng(263)


def log(*a):
    print(f"[{time.monotonic() - T0:6.0f} c]", *a, flush=True)


def ts(d, h=0):
    return int(datetime(d.year, d.month, d.day, h, tzinfo=MSK).timestamp())


# ------------------------------------------------------------------ данные
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

EMPTY = np.array([], dtype="int64")


def by_channel(pairs):
    g = {}
    for c, s in pairs:
        if int(c) in pos:
            g.setdefault(pos[int(c)], []).append(int(s))
    return {i: np.sort(np.array(v, dtype="int64")) for i, v in g.items()}


F = by_channel(zip(f.channel_id, f.s))  # реальные отказы активных каналов, по индексу


def groups(keys_of):
    """Отказы по группе (коллектор или пикет) — по всем каналам, и активным, и нет."""
    g = {}
    for k, s in zip(keys_of, f.s.to_numpy()):
        if k is not None:
            g.setdefault(k, []).append(int(s))
    return {k: np.sort(np.array(v)) for k, v in g.items()}


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


# ------------------------------------------------------------------ симуляция
def simulate(pf, ratio):
    fails, pre = failure_sim.simulate(passports, checks_by, DAY0, DAYN, pf_days=pf, false_ratio=ratio)
    S = by_channel((c, t.timestamp()) for c, t in fails)
    P = by_channel((c, t.timestamp()) for c, t, _ in pre)
    return fails, pre, S, P


log("симуляция: P-F 2 сут, 1 ложный на 1 истинный")
sim_fails, sim_pre, SIM, PRE = simulate(failure_sim.PF_DAYS, failure_sim.FALSE_RATIO)
with open(HERE / "sim_failures.csv", "w") as fh:
    fh.write("channel_id,started_at\n")
    for c, t in sim_fails:
        fh.write(f"{c},{t.isoformat(timespec='seconds')}\n")
with open(HERE / "sim_failures_precursors.csv", "w") as fh:
    fh.write("channel_id,observed_at,is_true\n")
    for c, t, ok in sim_pre:
        fh.write(f"{c},{t.isoformat(timespec='seconds')},{'t' if ok else 'f'}\n")
log(f"  отказов {len(sim_fails)}, предвестников {len(sim_pre)} (истинных {sum(ok for *_, ok in sim_pre)})")


# ------------------------------------------------------------------ моменты и признаки
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


def own_rearm(i):
    a = F.get(i, EMPTY)
    return [(a + CONFIRM, 1)] if len(a) else []


def picket_rearm(i):
    out = own_rearm(i)
    if PK[i] is not None:
        g = GP.get(PK[i], EMPTY)
        mine = set(F.get(i, EMPTY).tolist())
        other = np.array([s for s in g.tolist() if s not in mine], dtype="int64")
        if len(other):
            out.append((other + CONFIRM, 2))
    return out


def cnt(arr, lo_excl, hi_incl):
    return np.searchsorted(arr, hi_incl, "right") - np.searchsorted(arr, lo_excl, "right")


def real_features(mc, mt):
    X = np.zeros((len(mt), len(sensor_risk.REAL)), "float32")
    starts = np.searchsorted(mc, np.arange(C + 1))
    for i in range(C):
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        s = F.get(i, EMPTY)
        idx = np.searchsorted(s, t, "right")
        last = s[np.maximum(idx - 1, 0)] if len(s) else np.zeros(len(t), "int64")
        dd = np.where(idx > 0, np.minimum((t - last) / 86400, sensor_risk.CAP), sensor_risk.CAP)
        n7, n30, n90 = (idx - np.searchsorted(s, t - k * 86400, "right") for k in (7, 30, 90))
        gc = cnt(GC.get(COL[i], EMPTY), t - 7 * 86400, t) - n7 if COL[i] is not None else 0 * n7
        gp = cnt(GP.get(PK[i], EMPTY), t - 7 * 86400, t) - n7 if PK[i] is not None else 0 * n7
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


# ------------------------------------------------------------------ модель
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
    def __init__(self, mc, mt, mk, X):
        self.mc, self.mt, self.mk, self.X = mc, mt, mk, X

    def rows(self, m):
        """Строки для регрессии. У модели журнала свежий отказ (fresh = 1) отключает
        остальные признаки и вид датчика: все такие моменты получают одну вероятность,
        и модель на них повторяет правило «канал отказал час назад»."""
        x = np.hstack([self.X[m], onehot(self.mc[m])])
        if self.X.shape[1] == len(sensor_risk.REAL):
            fr = x[:, :1]
            x = np.hstack([fr, x[:, 1:] * (1 - fr)])
        return x


def fit(D, world, H, period):
    m, _, _ = window_mask(D.mt, period, H)
    y = labels(D.mc, D.mt, world, H)
    keep = m & (y | (D.mk > 0) | (rng.random(len(y)) < NEG_SHARE))
    wts = np.where(y[keep] | (D.mk[keep] > 0), 1.0, 1 / NEG_SHARE)
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
    return {"tp": tp, "fp": fp, "fn": fn, "alerts": len(at), "precision": p, "recall": r}


def one_open(ai, at, H, world):
    """Политика «одно открытое предупреждение», как у модели коллектора v3
    (ml-model/src/ml/moments.py): предупреждение по каналу открыто H часов или до
    первого отказа канала после него, и пока оно открыто, новое не выдаётся. Без неё
    срез 21:00 через несколько часов после отказа выдаёт второе предупреждение
    о том же риске. Без закрытия отказом политика глушила бы и сам rearm: на окне
    выбора правило теряло 13 попаданий из 34."""
    o = np.lexsort((at, ai))
    ai, at = ai[o], at[o]
    keep = np.zeros(len(at), bool)
    last_c, until = -1, 0
    for k, (c, t) in enumerate(zip(ai.tolist(), at.tolist())):
        if c != last_c or t >= until:
            keep[k] = True
            last_c = c
            s = world.get(c, EMPTY)
            j = np.searchsorted(s, t, "right")
            until = min(t + H * 3600, int(s[j]) if j < len(s) else 1 << 62)
    return ai[keep], at[keep]


def exact(ai, at, fails, H):
    """Числа в отчёт: evaluate_alerts() как есть, по каналам; сверка с fast()."""
    by = {}
    for i, t in zip(ai.tolist(), at.tolist()):
        by.setdefault(i, ([], []))[0].append((i, datetime.fromtimestamp(t, MSK)))
    for i, a in fails.items():
        for t in a.tolist():
            by.setdefault(i, ([], []))[1].append((i, datetime.fromtimestamp(t, MSK)))
    tp = fp = fn = dup = 0
    for a, b in by.values():
        m = evaluate_alerts(a, b, horizon_hours=0, max_lead_hours=H)
        tp, fp, fn, dup = tp + m["tp"], fp + m["fp"], fn + m["fn"], dup + m["dup"]
    g = fast(ai, at, fails, H)
    assert (g["tp"], g["fp"], g["fn"]) == (tp, fp, fn), (g, tp, fp, fn)
    n = sum(len(v) for v in fails.values())
    return {"tp": tp, "fp": fp, "fn": fn, "dup": dup, "alerts": len(at), "incidents": n,
            "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
            "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0}


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
            ok += cnt(t, s - H * 3600 - 1, s).item() > 0
    return round(ok / n, 3) if n else 0.0, ok, n


def curve(D, m, p, fails, H, world):
    order = np.argsort(-p)
    out = []
    for k in np.unique(np.geomspace(20, min(len(p), 400_000), 60).astype(int)):
        t = float(p[order[k - 1]])
        sel = p >= t
        r = fast(*one_open(D.mc[m][sel], D.mt[m][sel], H, world), fails, H)
        out.append({"threshold": t, **r, "f1": f1(r)})
    return out


def evaluate(D, m, p, fails, H, thr, world):
    """Модель: предупреждение, если p ≥ порога, и политика одного открытого."""
    sel = p >= thr
    return exact(*one_open(D.mc[m][sel], D.mt[m][sel], H, world), fails, H)


def choose(cv):
    hi = max(cv, key=lambda x: x["f1"])
    wa = min(cv, key=lambda x: abs(x["alerts"] - 10 * hi["alerts"]))
    return hi["threshold"], wa["threshold"]


def carry(model_sel, model_new, D, m, thr):
    """Порог после переобучения. Вероятности переученной модели сдвигаются, и число-
    порог первой модели на ней значит другое (так на проверке пропали все моменты
    rearm). Переносим не число, а долю: порог новой модели — такой, при котором
    на том же окне выбора она выдаёт столько же строк выше порога, сколько первая."""
    k = int((model_sel.proba(D.rows(m)) >= thr).sum())
    p = np.sort(model_new.proba(D.rows(m)))[::-1]
    return float(p[max(k, 1) - 1])


def p_fresh(model):
    """Вероятность свежего отказа у модели журнала: fresh = 1, остальное отключено."""
    return 1 / (1 + math.exp(-(model.intercept_ + model.coef_[0])))


def carry_real(model_sel, model_new, thr):
    """Порог модели журнала после переобучения — в долях вероятности свежего отказа.
    На окне выбора лучший F1 даёт «все свежие отказы и то, что не ниже их», и порог
    равен p_fresh первой модели; перенос по числу строк на переученной модели ставил
    порог выше блока свежих отказов (срезы 21:00 обгоняли его), и на проверке модель
    теряла все моменты rearm."""
    # допуск 1e-9 на округление: порог, равный p_fresh, не должен выйти выше неё
    return thr * p_fresh(model_new) / p_fresh(model_sel) * (1 - 1e-9)


def rule(D, m, kinds_):
    sel = m & np.isin(D.mk, kinds_)
    return D.mc[sel], D.mt[sel]


# ------------------------------------------------------------------ сверка признаков
def check_features(D, P=None):
    """Признаки скрипта против sensor_risk.features() на случайных моментах."""
    idx = np.concatenate([rng.choice(np.flatnonzero(D.mk > 0), 300), rng.integers(0, len(D.mt), 700)])
    for r in idx:
        i, t = int(D.mc[r]), int(D.mt[r])
        at = datetime.fromtimestamp(t, MSK)
        starts = [datetime.fromtimestamp(s, MSK) for s in F.get(i, EMPTY).tolist()]
        if P is None:
            nb = (int(round(math.expm1(D.X[r, 6]))), int(round(math.expm1(D.X[r, 7]))))
            got = [sensor_risk.features(starts, at, nb=nb)[k] for k in sensor_risk.REAL]
            # соседей в лоб по таблице отказов
            g = f[(f.s <= t) & (f.s > t - 7 * 86400)]
            n7 = sum(1 for s in F.get(i, EMPTY).tolist() if 0 <= t - s < 7 * 86400)
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


# ------------------------------------------------------------------ прогон
run = {"horizons": {}, "sim": {}, "chosen": None, "text": {}}

log("моменты без синтетики: tick + rearm своего отказа; вариант — плюс rearm соседа по пикету")
DR = {}
for name, extra in (("own", own_rearm), ("picket", picket_rearm)):
    mc, mt, mk = moments(extra)
    DR[name] = Design(mc, mt, mk, real_features(mc, mt))
    log(f"  {name}: моментов {len(mt):,}, из них rearm {int((mk == 1).sum()):,}, соседа {int((mk == 2).sum()):,}")
check_features(DR["picket"])
log("  признаки без синтетики совпали с sensor_risk.features() на 1 000 моментах")

log("моменты модели симулированных отказов: tick + через час после предвестника")


def sim_design(P):
    mc, mt, mk = moments(lambda i: [(P[i] + CONFIRM, 3)] if i in P else [])
    return Design(mc, mt, mk, sim_features(mc, mt, P))


DS = sim_design(PRE)
check_features(DS, PRE)
log("  признаки паспорта и предвестника совпали с sensor_risk.features() на 1 000 моментах")

# числа для текстов (п. 6 доработки): считаются здесь, чтобы их можно было повторить
for name, per in (("select", SELECT), ("test", TEST)):
    _, lo, hi = window_mask(DR["own"].mt, per, 12)
    fl = failures_in(F, lo, hi, 12)
    first = sum(int((F[i] < s).sum() == 0) for i, a in fl.items() for s in a.tolist())
    coll = 0
    for i, a in fl.items():
        for s in a.tolist():
            coll += COL[i] is not None and cnt(GC[COL[i]], s - 86400 - 1, s - 1).item() - cnt(F[i], s - 86400 - 1, s - 1).item() > 0
    tick = DR["own"].mk == 0
    n = sum(len(a) for a in fl.values())
    run["text"][name] = {"failures": n, "first_ever": first, "coll_prior_24h": int(coll),
                         "night_ceiling_12h": ceiling(DR["own"], tick, fl, 12)}
    log(f"  {name}: отказов {n}, первый у канала с 2022-04-01 {first}, на коллекторе был отказ за 24 ч до {coll}, "
        f"потолок Recall tick 21:00 при 12 ч {run['text'][name]['night_ceiling_12h']}")

log("выбор на 2026-01-01…2026-03-31")
models = {}
for H in HORIZONS:
    run["horizons"][H] = {}
    for name, D in DR.items():
        m, lo, hi = window_mask(D.mt, SELECT, H)
        fails = failures_in(F, lo, hi, H)
        model = fit(D, F, H, TRAIN)
        models[("real", H, name)] = model
        p = model.proba(D.rows(m))
        cv = curve(D, m, p, fails, H, F)
        hi_t, wa_t = choose(cv)
        res = {"high": hi_t, "watch": wa_t, "model": evaluate(D, m, p, fails, H, hi_t, F),
               "rule_rearm": exact(*rule(D, m, [1]), fails, H),
               "rule_rearm_one_open": exact(*one_open(*rule(D, m, [1]), H, F), fails, H),
               "curve": cv, "ceiling_all": ceiling(D, m, fails, H),
               "ceiling_tick": ceiling(D, m & (D.mk == 0), fails, H)}
        if name == "picket":
            res["rule_rearm_picket"] = exact(*one_open(*rule(D, m, [1, 2]), H, F), fails, H)
        run["horizons"][H][name] = res
        log(f"  без синтетики, {name}, H={H}: модель {res['model']}, правило rearm {res['rule_rearm']}")
    # модель симулированных отказов: метрики только на симулированных отказах
    m, lo, hi = window_mask(DS.mt, SELECT, H)
    fails = failures_in(SIM, lo, hi, H)
    model = fit(DS, SIM, H, TRAIN)
    models[("sim", H)] = model
    p = model.proba(DS.rows(m))
    cv = curve(DS, m, p, fails, H, SIM)
    hi_t, wa_t = choose(cv)
    run["horizons"][H]["sim"] = {"high": hi_t, "watch": wa_t, "model": evaluate(DS, m, p, fails, H, hi_t, SIM), "curve": cv}
    log(f"  симуляция H={H}: модель {run['horizons'][H]['sim']['model']}")

best = max(((H, n) for H in HORIZONS for n in DR), key=lambda x: f1(run["horizons"][x[0]][x[1]]["model"]))
Hb, variant = best
run["chosen"] = {"horizon": Hb, "moments": variant}
log(f"выбрано: {Hb} ч, моменты {variant}; переучиваем на 2022-04-01…2026-03-31 и меряем апрель–июнь")

D = DR[variant]
sel = run["horizons"][Hb][variant]
real_model = fit(D, F, Hb, REFIT)
msel, _, _ = window_mask(D.mt, SELECT, Hb)
sel = {**sel, "high": carry_real(models[("real", Hb, variant)], real_model, sel["high"]),
       "watch": carry_real(models[("real", Hb, variant)], real_model, sel["watch"])}
run["chosen"]["p_fresh"] = {"select": p_fresh(models[("real", Hb, variant)]), "refit": p_fresh(real_model)}
run["chosen"]["thresholds_real"] = {"high": sel["high"], "watch": sel["watch"]}
m, lo, hi = window_mask(D.mt, TEST, Hb)
fails = failures_in(F, lo, hi, Hb)
p = real_model.proba(D.rows(m))
run["test"] = {
    "cuts": [str(datetime.fromtimestamp(lo, MSK)), str(datetime.fromtimestamp(hi, MSK))],
    "real": {
        "model": evaluate(D, m, p, fails, Hb, sel["high"], F),
        "model_watch": evaluate(D, m, p, fails, Hb, sel["watch"], F),
        "model_rearm_only": evaluate(D, m & (D.mk > 0), real_model.proba(D.rows(m & (D.mk > 0))), fails, Hb, sel["high"], F),
        "rule_rearm": exact(*rule(D, m, [1]), fails, Hb),
        "rule_rearm_one_open": exact(*one_open(*rule(D, m, [1]), Hb, F), fails, Hb),
        "rule_tick_1d": None,
        "ceiling_all": ceiling(D, m, fails, Hb),
        "ceiling_tick": ceiling(D, m & (D.mk == 0), fails, Hb),
    },
}
# прежнее правило на tick 21:00: отказ канала за последние сутки
tk = m & (D.mk == 0)
recent = D.X[:, 1] > math.exp(-1) - 1e-9  # r1 = exp(−d), d ≤ 1 сут
run["test"]["real"]["rule_tick_1d"] = exact(D.mc[tk & recent], D.mt[tk & recent], fails, Hb)
if variant == "picket":
    run["test"]["real"]["rule_rearm_picket"] = exact(*one_open(*rule(D, m, [1, 2]), Hb, F), fails, Hb)
log(f"  тест без синтетики: {run['test']['real']}")

# уровни на срезах: сколько high и watch у парка на tick 21:00
levels = {}
for d in (date(2026, 6, 23), date(2026, 6, 29)):
    mm = D.mt == ts(d, 21)
    pr = real_model.proba(D.rows(mm))
    levels[str(d)] = {"high": int((pr >= sel["high"]).sum()), "watch": int(((pr >= sel["watch"]) & (pr < sel["high"])).sum())}
tkt = m & (D.mk == 0)
ptk = real_model.proba(D.rows(tkt))
per_tick = pd.Series(ptk >= sel["high"]).groupby(D.mt[tkt]).sum()
levels["test_ticks_high_median"] = float(per_tick.median())
levels["test_ticks_high_max"] = int(per_tick.max())
run["test"]["levels_real"] = levels
log(f"  уровни без синтетики на tick 21:00: {levels}")

# симуляция на выбранном горизонте: итог, потолки, чувствительность
ss = run["horizons"][Hb]["sim"]
sim_model = fit(DS, SIM, Hb, REFIT)
msel, _, _ = window_mask(DS.mt, SELECT, Hb)
ss = {**ss, "high": carry(models[("sim", Hb)], sim_model, DS, msel, ss["high"]),
      "watch": carry(models[("sim", Hb)], sim_model, DS, msel, ss["watch"])}
run["chosen"]["thresholds_sim"] = {"high": ss["high"], "watch": ss["watch"]}
m, lo, hi = window_mask(DS.mt, TEST, Hb)
fails = failures_in(SIM, lo, hi, Hb)
p = sim_model.proba(DS.rows(m))
# потолок без предвестника: истинная вероятность генератора на моментах tick
tk = m & (DS.mk == 0)
true_p = []
tk_c, tk_t = DS.mc[tk], DS.mt[tk]
tk_starts = np.searchsorted(tk_c, np.arange(C + 1))
for i in range(C):
    c = int(cid[i])
    beta, eta, iv, mult = failure_sim.params_of(passports[c])
    pday = {}
    for t in tk_t[tk_starts[i]:tk_starts[i + 1]].tolist():
        d0 = datetime.fromtimestamp(t, MSK).date()
        q = 1.0
        for k in range(math.ceil(Hb / 24) + 1):
            d = d0 + timedelta(days=k)
            if d not in pday:
                pday[d] = failure_sim.day_prob(d, passports[c]["in_service"], beta, eta, checks_by.get(c, ()), iv, mult)
            share = min(max((t + Hb * 3600 - ts(d)) / 86400, 0), 1) - min(max((t - ts(d)) / 86400, 0), 1)
            q *= (1 - pday[d]) ** share
        true_p.append(1 - q)
true_p = np.sort(np.array(true_p))[::-1]
oracle = [p_ for p_ in (true_p[:20].mean(), true_p[:100].mean(), true_p[:1000].mean())]
pf = failure_sim.PF_DAYS * 86400
pre_ok = np.zeros(m.sum(), bool)
mc_, mt_ = DS.mc[m], DS.mt[m]
m_starts = np.searchsorted(mc_, np.arange(C + 1))
for i, pr in PRE.items():
    a, b = m_starts[i], m_starts[i + 1]
    pre_ok[a:b] = cnt(pr, mt_[a:b] - pf, mt_[a:b] - pf + Hb * 3600) > 0
run["test"]["sim"] = {
    "model": evaluate(DS, m, p, fails, Hb, ss["high"], SIM),
    "model_watch": evaluate(DS, m, p, fails, Hb, ss["watch"], SIM),
    "oracle_no_precursor_precision": {"top20": oracle[0], "top100": oracle[1], "top1000": oracle[2], "max": float(true_p[0])},
    "oracle_precursor": exact(*one_open(mc_[pre_ok], mt_[pre_ok], Hb, SIM), fails, Hb),
    "ceiling_all": ceiling(DS, m, fails, Hb),
}
log(f"  тест симуляции: {run['test']['sim']}")

run["sensitivity"] = []
for pf_, ratio in SENS:
    _, _, S2, P2 = simulate(pf_, ratio)
    D2 = sim_design(P2)
    msel, lo, hi = window_mask(D2.mt, SELECT, Hb)
    mdl = fit(D2, S2, Hb, TRAIN)
    cv = curve(D2, msel, mdl.proba(D2.rows(msel)), failures_in(S2, lo, hi, Hb), Hb, S2)
    thr, _ = choose(cv)
    mdl0 = mdl
    mdl = fit(D2, S2, Hb, REFIT)
    thr = carry(mdl0, mdl, D2, msel, thr)
    mt_m, lo, hi = window_mask(D2.mt, TEST, Hb)
    fl2 = failures_in(S2, lo, hi, Hb)
    res = evaluate(D2, mt_m, mdl.proba(D2.rows(mt_m)), fl2, Hb, thr, S2)
    run["sensitivity"].append({"pf_days": pf_, "false_ratio": ratio, "model": res})
    log(f"  чувствительность P-F {pf_} сут, ложных {ratio}: {res}")


def sig(x):
    """Шесть значащих цифр: порог 6,4·10⁻⁷ при round(x, 6) превращался в 10⁻⁶."""
    return float(f"{x:.6g}")


def export(model, names):
    return {"intercept": model.intercept_,
            "coef": {n: round(float(v), 6) for n, v in zip(names, model.coef_)},
            "kind": {k: round(float(v), 6) for k, v in zip(kinds, model.coef_[len(names):])}}


out = {
    "version": f"sensor-lr-h{Hb}-{variant}-2026.09.28",
    "horizon_h": Hb,
    "moments": variant,
    "trained": [str(REFIT[0]), str(REFIT[1] - timedelta(days=1))],
    "modes": {
        "real": {**export(real_model, sensor_risk.REAL), "gate": "fresh",
                 "thresholds": {"high": sig(sel["high"]), "watch": sig(sel["watch"])}},
        "sim": {**export(sim_model, sensor_risk.SIM),
                "thresholds": {"high": sig(ss["high"]), "watch": sig(ss["watch"])},
                "pf_days": failure_sim.PF_DAYS, "false_ratio": failure_sim.FALSE_RATIO},
    },
}
out["modes"]["real"]["intercept"] = round(out["modes"]["real"]["intercept"], 6)
out["modes"]["sim"]["intercept"] = round(out["modes"]["sim"]["intercept"], 6)
# экспорт против предсказания обученных моделей: sensor_risk._logit на тех же строках
for mode, model, DD in (("real", real_model, D), ("sim", sim_model, DS)):
    names = sensor_risk.REAL if mode == "real" else sensor_risk.SIM
    idx = np.concatenate([rng.integers(0, len(DD.mt), 300), rng.choice(np.flatnonzero(DD.mk > 0), 100)])
    for r in idx:
        x = dict(zip(names, DD.X[r].astype(float)))
        z = sensor_risk._logit(out["modes"][mode], x, kinds[KIND[DD.mc[r]]])
        one = np.zeros(len(DD.mt), bool)
        one[r] = True
        want = model.proba(DD.rows(one))[0]
        assert abs(1 / (1 + math.exp(-z)) - want) < 1e-5 * max(want, 1e-3)
(ROOT / "backend/app/domain/sensor_model.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
(HERE / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1, default=float) + "\n")
log("готово: backend/app/domain/sensor_model.json, run.json")
