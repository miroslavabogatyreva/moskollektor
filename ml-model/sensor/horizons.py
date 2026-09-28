"""Замер правил датчика на горизонтах 12 ч, 24 ч, 3, 7, 14 сут. Только чтение CSV.

Методика PR #73 (rules.py): срез 21:00 МСК каждых суток, evaluate_alerts через T.exact,
параметры выбираются на Q1 2026, замер на Q2 2026. Обучающее окно 2022-04-01…2025-12-31
тут не нужно: правила без балла, только пороги.

Запуск из корня репозитория: python3 ml-model/sensor/horizons.py
(пишет ml-model/sensor/horizons.json)
(numpy и pandas — как у rules.py; данные — ml-model/sensor/data/ из export_data.sh)
"""

import importlib.util
import json
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
PROOF = ROOT / "ml-model/sensor"
spec = importlib.util.spec_from_file_location("rules", PROOF / "rules.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
T, fs = R.T, R.fs

HS = [12, 24, 72, 168, 336]
GRID_D = [1, 2, 3, 7, 14, 30, 60, 90]  # сутки
GRID_KW = list(product([2, 3, 4, 5], [30, 90, 180]))


def count_recent(mc, mt, W):
    """Подтверждённых отказов канала с началом в (t − W сут, t − CONFIRM]."""
    out = np.zeros(len(mt), "int32")
    starts = np.searchsorted(mc, np.arange(T.C + 1))
    for i, s in T.F.items():
        a, b = starts[i], starts[i + 1]
        t = mt[a:b]
        out[a:b] = T.cnt(s, t - W * 86400, t - T.CONFIRM)
    return out


def pick(cands):
    """cands: [(имя, параметры, маска, метрика Q1)] → лучший по F1 и лучший по P при R ≥ 0,1."""
    by_f1 = max(cands, key=lambda c: (c[3]["f1"], c[3]["precision"]))
    ok = [c for c in cands if c[3]["recall"] >= 0.1]
    by_p = max(ok, key=lambda c: (c[3]["precision"], c[3]["recall"])) if ok else None
    return by_f1, by_p


def slim(r):
    return {k: r[k] for k in ("tp", "fp", "fn", "alerts", "precision", "recall", "f1")}


def random_baseline(mc, mt, n, fails, H, seeds=20):
    """Случайные n пар (канал, срез) из окна; средний Precision по тем же правилам счёта."""
    rng = np.random.default_rng(263)
    ps = []
    for _ in range(seeds):
        k = rng.choice(len(mt), n, replace=False)
        ps.append(T.fast(mc[k], mt[k], fails, H)["precision"])
    return float(np.mean(ps))


def main():
    T.load_data()
    _, _, SIM, PRE = T.simulate(fs.PF_DAYS, fs.FALSE_RATIO)
    pf = int(fs.PF_DAYS * 86400)
    mc, mt, _ = T.moments(lambda i: [])
    since = R.hours_since(mc, mt)
    due = R.precursor_due(mc, mt, PRE, pf)
    cnts = {W: count_recent(mc, mt, W) for W in (30, 90, 180)}
    res = {}
    for H in HS:
        q1, lo1, hi1 = T.window_mask(mt, T.SELECT, H)
        q2, lo2, hi2 = T.window_mask(mt, T.TEST, H)
        f1r = T.failures_in(T.F, lo1, hi1, H)
        f2r = T.failures_in(T.F, lo2, hi2, H)
        f2s = T.failures_in(SIM, lo2, hi2, H)
        both = {i: np.sort(np.concatenate([f2r.get(i, T.EMPTY), f2s.get(i, T.EMPTY)]))
                for i in set(f2r) | set(f2s)}

        def q1m(mask):
            r = T.fast(mc[q1 & mask], mt[q1 & mask], f1r, H)
            return r | {"f1": T.f1(r)}

        rec = {D: since < D * 24 for D in GRID_D}
        frq = {(K, W): cnts[W] >= K for K, W in GRID_KW}
        fam = {
            "давность": [("давность", f"D={D} сут", m) for D, m in rec.items()],
            "частота": [("частота", f"K≥{K} за {W} сут", m) for (K, W), m in frq.items()],
            "давность ИЛИ частота": [
                ("комбо", f"D={D} сут ИЛИ K≥{K} за {W} сут", rec[D] | frq[(K, W)])
                for D in GRID_D for K, W in GRID_KW],
        }
        rows = []
        for name, cands in fam.items():
            cands = [(a, p, m, q1m(m)) for a, p, m in cands]
            for crit, c in zip(("F1", "P@R≥0,1"), pick(cands)):
                if c is None:
                    rows.append({"rule": name, "crit": crit, "params": "нет порога с R≥0,1 на Q1"})
                    continue
                m = q2 & c[2]
                r = slim(T.exact(mc[m], mt[m], f2r, H))
                rows.append({"rule": name, "crit": crit, "params": c[1], "q1": slim(c[3]),
                             "q2": r, "_mask": c[2]})
        # предвестник (синтетика): due ≤ H — отказ, назначенный наблюдённым предупреждением,
        # наступит в ближайшие H часов (для H ≥ P-F — любое открытое предупреждение)
        pre = due <= H * 3600
        m = q2 & pre
        rows.append({"rule": "предвестник (на 312 симулированных)", "crit": "—",
                     "params": f"P-F {fs.PF_DAYS} сут", "q2": slim(T.exact(mc[m], mt[m], f2s, H)),
                     "world": "sim"})
        rec_best = next(r for r in rows if r["rule"] == "давность" and r["crit"] == "F1")
        m = q2 & (rec_best["_mask"] | pre)
        rows.append({"rule": "давность ИЛИ предвестник (реальные + симулированные)", "crit": "F1 давности",
                     "params": rec_best["params"] + f" ИЛИ предвестник", "world": "both",
                     "q2": slim(T.exact(mc[m], mt[m], both, H))})
        # базовые линии: доля (канал, срез) Q2 с отказом в (t, t+H] и случайная выборка того же размера
        worlds = {"real": f2r, "sim": f2s, "both": both}
        share = {w: float(T.labels(mc, mt, f, H)[q2].mean()) for w, f in worlds.items()}
        for r in rows:
            if "q2" not in r:
                continue
            w = r.get("world", "real")
            r["base_share"] = share[w]
            r["base_random"] = random_baseline(mc[q2], mt[q2], r["q2"]["alerts"], worlds[w], H)
            r["lift"] = r["q2"]["precision"] / r["base_random"] if r["base_random"] else None
            r.pop("_mask", None)
        res[H] = {
            "q2_cuts": [datetime.fromtimestamp(t, T.MSK).isoformat() for t in (lo2, hi2 + H * 3600)],
            "q2_real_failures": sum(map(len, f2r.values())),
            "q2_real_channels": len(f2r),
            "q2_sim_failures": sum(map(len, f2s.values())),
            "q2_ticks": int(q2.sum() // T.C),
            "rows": rows,
        }
        # пример TP: лучшая давность по F1, последнее предупреждение перед отказом
        ex = None
        crit = next(r for r in rows if r["rule"] == "давность" and r["crit"] == "F1")["params"]
        D = int(crit.split("=")[1].split()[0])
        starts = np.searchsorted(mc, np.arange(T.C + 1))
        for i in sorted(f2r):
            a, b = starts[i], starts[i + 1]
            t, ok = mt[a:b], (q2 & (since < D * 24))[a:b]
            for fail in f2r[i].tolist():
                k = np.flatnonzero(ok & (t < fail) & (t >= fail - H * 3600))
                if len(k):
                    k = a + k[-1]
                    ex = {"channel_id": int(T.cid[i]),
                          "alert": datetime.fromtimestamp(int(mt[k]), T.MSK).isoformat(),
                          "hours_since_prev_failure": round(float(since[k]), 1),
                          "failure_start": datetime.fromtimestamp(int(fail), T.MSK).isoformat()}
                    break
            if ex:
                break
        res[H]["example_tp"] = ex
        T.log(f"H={H}: готово")
    out = PROOF / "horizons.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float) + "\n")
    T.log(f"готово: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
