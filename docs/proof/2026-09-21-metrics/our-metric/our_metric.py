#!/usr/bin/env python3
"""Метрики модели Николая на НАШЕЙ методике: ключ — коллектор, окно 24…168 ч."""
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

D = Path("/private/tmp/claude-501/-Users-miroslavabogatyreva-Projects-moskollektor/"
         "9f25a868-89f8-49b5-8b6e-dd8de95df54e/scratchpad/our-metric")
V = Path("/private/tmp/claude-501/-Users-miroslavabogatyreva-Projects-moskollektor/"
         "9f25a868-89f8-49b5-8b6e-dd8de95df54e/scratchpad/verify/lct-2026-task8")
PM_PATH = Path("/Users/miroslavabogatyreva/Projects/moskollektor/code/predictive_metrics.py")

spec = importlib.util.spec_from_file_location("pm", PM_PATH)
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)

LO = pd.Timestamp("2026-04-01")
BODY_HI = pd.Timestamp("2026-06-01")          # тело: 2026-04-01 … 2026-05-31
HI = BODY_HI + pd.Timedelta(hours=720)        # 2026-07-01

# --- связка канал -> коллектор, префикс -> коллектор -----------------------------
m = pd.read_csv(D / "ch2collector.csv", dtype={"pfx": str})
ch2col = {int(r.channel_id): int(r.collector)
          for r in m.dropna(subset=["collector"]).itertuples()}
pfx2col = (m.dropna(subset=["collector"]).groupby("pfx").collector
           .agg(lambda s: sorted({int(x) for x in s})).to_dict())
AMBIG = {p for p, c in pfx2col.items() if len(c) > 1}
DROP_COL = {c for p in AMBIG for c in pfx2col[p]}

key = pm.collector_key(ch2col)


def incidents(failures):
    """failures: [(ch, Timestamp)] -> [(ключ коллектора, t)] в окне блока."""
    inc = pm.group_incidents(failures, window_minutes=10, group_key=key)
    return [(k, t) for k, t in inc if LO < t < HI]


# --- отказы двух словарей --------------------------------------------------------
db = pd.read_csv(D / "fails_db.csv", parse_dates=["t_start"])
fail_our = list(zip(db.channel_id.astype(int), db.t_start.dt.to_pydatetime()))
d5 = pd.read_parquet(V / "data/03_processed/v3_final_20260920/failures.parquet")
d5 = d5[(d5.t_start >= "2026-03-20") & (d5.t_start < "2026-07-02")]
fail_d5 = list(zip(d5.ch.astype(int), d5.t_start.dt.to_pydatetime()))

inc_our, inc_d5 = incidents(fail_our), incidents(fail_d5)
print("отказов: наш словарь", len(fail_our), "| D5", len(fail_d5))
print("инцидентов в окне: наш словарь", len(inc_our), "| D5", len(inc_d5))

# --- предупреждения --------------------------------------------------------------
al = pd.read_csv(D / "alerts.csv", parse_dates=["t"], dtype={"pfx": str})
print("предупреждений всего", len(al), "в теле", int(al.body.sum()))


def alerts_on_collectors(mode):
    out = []
    for r in al.itertuples():
        cols = pfx2col.get(r.pfx, [])
        if mode == "drop" and r.pfx in AMBIG:
            continue
        for c in cols:
            if mode == "drop" and c in DROP_COL:
                continue
            out.append((f"obj:{c}", r.t))
    return out


def filt_inc(inc, mode):
    if mode != "drop":
        return inc
    return [(k, t) for k, t in inc if k not in {f"obj:{c}" for c in DROP_COL}]


def row(name, unit, dic, window, alerts, inc, h, mx):
    m_ = pm.evaluate_alerts(alerts, inc, horizon_hours=h, max_lead_hours=mx)
    return {"методика": name, "единица": unit, "словарь": dic, "окно": window,
            "инцидентов": len(inc), "предупреждений": len(alerts),
            "TP": m_["tp"], "FP": m_["fp"], "FN": m_["fn"],
            "P": round(m_["tp"] / (m_["tp"] + m_["fp"]), 3) if m_["tp"] + m_["fp"] else 0,
            "R": round(m_["tp"] / (m_["tp"] + m_["fn"]), 3) if m_["tp"] + m_["fn"] else 0}


rows = []
A = alerts_on_collectors("both")
rows.append(row("наша", "коллектор", "наш «Неисправен»", "24…168", A, inc_our, 24, 168))
rows.append(row("наша", "коллектор", "его D5", "24…168", A, inc_d5, 24, 168))
rows.append(row("наша", "коллектор", "наш «Неисправен»", "24…720", A, inc_our, 24, 720))
rows.append(row("наша", "коллектор", "наш «Неисправен»", "0…720 (его окно)", A, inc_our, 0, 720))
Ad = alerts_on_collectors("drop")
rows.append(row("наша, без 163 и 798", "коллектор", "наш «Неисправен»", "24…168",
                Ad, filt_inc(inc_our, "drop"), 24, 168))
# его единица (pfx), наше окно
inc_pfx = pd.read_csv(D / "incidents_pfx.csv", parse_dates=["t_start"], dtype={"pfx": str})
A_pfx = list(zip(al.pfx, al.t.dt.to_pydatetime()))
F_pfx = list(zip(inc_pfx.pfx, inc_pfx.t_start.dt.to_pydatetime()))
rows.append(row("наша", "префикс (его)", "его D5", "24…168", A_pfx, F_pfx, 24, 168))
rows.append(row("его", "префикс", "его D5", "0…720", A_pfx, F_pfx, 0, 720))

tab = pd.DataFrame(rows)
print(tab.to_markdown(index=False))
tab.to_csv(D / "table.csv", index=False)

# --- М-20: упреждение ------------------------------------------------------------
for nm, alerts, inc in (("наш ключ+словарь, окно 0…168", A, inc_our),
                        ("наш ключ, D5, окно 0…168", A, inc_d5)):
    mm = pm.evaluate_alerts(alerts, inc, horizon_hours=0, max_lead_hours=168)
    print(nm, "медиана упреждения, ч:", mm["median_lead_hours"],
          "| мин:", mm["min_lead_hours"],
          "| доля <24 ч:", mm["lead_under_24h_share"], "| TP", mm["tp"])

# --- цена допущения по неоднозначным префиксам -----------------------------------
print("неоднозначные префиксы:", {p: pfx2col[p] for p in AMBIG})
print("предупреждений на них:", int(al.pfx.isin(AMBIG).sum()), "из", len(al))
print("инцидентов на их коллекторах (наш словарь):",
      sum(1 for k, _ in inc_our if k in {f"obj:{c}" for c in DROP_COL}), "из", len(inc_our))

# --- геометрия «хвост» для нашего окна: Precision по предупреждениям тела ---------
# Тело кончается за 168 ч до конца данных (2026-07-01), иначе окно предупреждения
# обрезано концом выгрузки и его нечем подтвердить.
BODY_OUR = pd.Timestamp("2026-07-01") - pd.Timedelta(hours=168)


def tail_row(name, alerts_df, inc, dic, h=24, mx=168):
    body = [(f"obj:{c}", r.t) for r in alerts_df.itertuples()
            for c in pfx2col.get(r.pfx, []) if r.t < BODY_OUR]
    allal = [(f"obj:{c}", r.t) for r in alerts_df.itertuples()
             for c in pfx2col.get(r.pfx, [])]
    mb = pm.evaluate_alerts(body, inc, horizon_hours=h, max_lead_hours=mx)
    ma = pm.evaluate_alerts(allal, inc, horizon_hours=h, max_lead_hours=mx)
    p = mb["tp"] / (mb["tp"] + mb["fp"]) if mb["tp"] + mb["fp"] else 0
    r = ma["tp"] / (ma["tp"] + ma["fn"]) if ma["tp"] + ma["fn"] else 0
    print({"строка": name, "словарь": dic, "тело до": str(BODY_OUR.date()),
           "предупреждений тела": len(body), "всего": len(allal),
           "инцидентов": len(inc), "tp_P": mb["tp"], "fp": mb["fp"],
           "tp_R": ma["tp"], "fn": ma["fn"], "P": round(p, 3), "R": round(r, 3)})


tail_row("наша, геометрия tail", al, inc_our, "наш «Неисправен»")
tail_row("наша, геометрия tail", al, inc_d5, "его D5")
