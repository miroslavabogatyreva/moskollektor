#!/usr/bin/env python3
"""H=720, геометрия tail, правило margin: повтор основной строки + список предупреждений."""
import importlib.util
import os
import sys
from pathlib import Path

R = Path("/private/tmp/claude-501/-Users-miroslavabogatyreva-Projects-moskollektor/"
         "9f25a868-89f8-49b5-8b6e-dd8de95df54e/scratchpad/verify/lct-2026-task8")
OUT = Path("/private/tmp/claude-501/-Users-miroslavabogatyreva-Projects-moskollektor/"
           "9f25a868-89f8-49b5-8b6e-dd8de95df54e/scratchpad/our-metric")
os.chdir(R)
sys.path.insert(0, str(R / "src"))
spec = importlib.util.spec_from_file_location("hold", R / "scripts" / "ml_v3_holdout.py")
hold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hold)

H, GEO, RULE = 720, "tail", "margin"
prepare, train = hold._setup([], hold.FINAL_DATA)
inc_all = prepare.load_incidents()
rows = hold._load_rows(prepare, H)
donors = hold.validation_blocks(prepare, rows, inc_all, H)
donor_scores = [hold.score_block(train, b) for b in donors]
print("доноры посчитаны", flush=True)
test = hold.make_block(prepare, rows, inc_all, hold.TEST_FROM,
                       hold.score_end(hold.TEST_END, H), H)
test_scores = hold.score_block(train, test)
print("test посчитан", flush=True)
grids = [hold.donor_grid(prepare, s, b, GEO) for s, b in zip(donor_scores, donors)]
thr = hold.pick_threshold(RULE, grids, prepare.TARGET_P, prepare.TARGET_R)
print("порог", thr, flush=True)
res = hold.judge(prepare, test_scores, test, thr, GEO)
print("судимая строка:", res, flush=True)

al = prepare.simulate(test_scores, test["incidents"], thr, H)
al = al.assign(body=(al["t"] < test["body_hi"]))
al.to_csv(OUT / "alerts.csv", index=False)
test["incidents"].to_csv(OUT / "incidents_pfx.csv", index=False)
(OUT / "run_meta.txt").write_text(
    f"H={H} geometry={GEO} rule={RULE} threshold={thr}\n"
    f"body_hi={test['body_hi']}\nalerts={len(al)} body={int(al['body'].sum())}\n"
    f"incidents={len(test['incidents'])}\njudge={res}\n")
print("записано", len(al), "предупреждений", flush=True)
