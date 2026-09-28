"""Скоры моментов test-блока (01.04–30.06.2026) модели v3, H=720 — тот же путь, что run_alerts.py 21.09.
Порог не подбирается (0,63 из model_meta), доноры не считаются."""
import importlib.util, sys, time
from pathlib import Path
R = Path.cwd()
sys.path.insert(0, str(R / "src"))
spec = importlib.util.spec_from_file_location("hold", R / "scripts" / "ml_v3_holdout.py")
hold = importlib.util.module_from_spec(spec); spec.loader.exec_module(hold)
H = 720
t0 = time.time()
prepare, train = hold._setup([], hold.FINAL_DATA)
inc_all = prepare.load_incidents()
rows = hold._load_rows(prepare, H)
test = hold.make_block(prepare, rows, inc_all, hold.TEST_FROM, hold.score_end(hold.TEST_END, H), H)
sc = hold.score_block(train, test)
out = Path(sys.argv[1])
sc.to_csv(out / "scores_test.csv", index=False)
test["incidents"].to_csv(out / "incidents_pfx.csv", index=False)
al = prepare.simulate(sc, test["incidents"], 0.63, H)
al.to_csv(out / "alerts_rerun.csv", index=False)
print("body_hi", test["body_hi"], "moments", len(sc), "alerts", len(al), f"{time.time()-t0:.0f} s")
