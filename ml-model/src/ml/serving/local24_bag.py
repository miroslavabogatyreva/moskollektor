"""Calibrated section-level 24-hour ensemble; no collector probability spreading."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import lightgbm as lgb
import numpy as np

FORMAT = "local24.bag.v1"


class Bag:
    def __init__(self, directory: Path, meta: dict):
        if meta.get("horizon_h") != 24 or meta.get("object_level") != "section":
            raise ValueError("local24 requires section-level next-24h target")
        self.specs = meta["boosters"]
        if not self.specs: raise ValueError("Empty ensemble")
        digest = hashlib.sha256(json.dumps(self.specs, sort_keys=True).encode()).hexdigest()
        if digest != meta["sha256"]: raise ValueError("Model manifest hash mismatch")
        self.boosters = []
        for s in self.specs:
            if Path(s["file"]).name != s["file"]: raise ValueError("Unsafe model filename")
            path = directory / s["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != s["sha256"]:
                raise ValueError("Model weight hash mismatch")
            c = s["calibration"]
            if not np.isfinite([c["coef"], c["intercept"]]).all() or c["coef"] <= 0:
                raise ValueError("Invalid calibration")
            b = lgb.Booster(model_file=str(path))
            if b.feature_name() != meta["feature_names"]: raise ValueError("Feature schema mismatch")
            self.boosters.append(b)

    def predict_proba(self, x):
        probs = []
        for b,s in zip(self.boosters,self.specs):
            c=s["calibration"]
            z=np.clip(c["coef"]*b.predict(x,raw_score=True,num_threads=6)+c["intercept"],-35,35)
            probs.append(1/(1+np.exp(-z)))
        return np.mean(probs,axis=0)

    def contributions(self,x):
        # Explicitly mean raw logit, not calibrated ensemble probability.
        raw=np.mean([b.predict(x,raw_score=True,num_threads=6) for b in self.boosters],axis=0)
        shap=np.mean([b.predict(x,pred_contrib=True,num_threads=6) for b in self.boosters],axis=0)
        return raw,shap
