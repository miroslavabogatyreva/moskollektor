"""Модель протокола v3 на сервере: мешок бустеров, голова `rearm` и калибровка по видам.

Состав тот же, что у исторического обучения v3:

1. медиана вероятностей 25 бустеров LightGBM (у каждого своё число деревьев);
2. голова `rearm` (горизонт от 336 ч): у строк `is_rearm = 1` полусумма логитов бустеров
   и логистической регрессии по `log1p` трёх счётчиков событий;
3. калибровка Платта отдельно для `rearm` и для тиков.

Логистические части — это `sigmoid(w · x + b)`: коэффициенты лежат в `model_meta.json`,
scikit-learn на сервере не нужен. Совпадение с обучением до 1e-9 проверяет
исторический экспорт v3 при выгрузке.

Вклады признаков — среднее `pred_contrib` бустеров. Они в сумме дают средний логит мешка
до головы и калибровки; вероятность ответа считается по медиане. Объяснение показывает,
что двигало бустерами, а не калибровку.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np

FORMAT = "v3-bag"


def _logit(p: np.ndarray) -> np.ndarray:
    # тот же клип, что в train.py::_logit: иначе крайние вероятности разойдутся
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def bag_sha256(directory: Path, files: list[str]) -> str:
    """Один отпечаток на мешок: SHA-256 файлов бустеров подряд, в порядке описания."""
    digest = hashlib.sha256()
    for name in files:
        digest.update((Path(directory) / name).read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Bag:
    boosters: tuple[lgb.Booster, ...]
    n_trees: tuple[int, ...]
    feature_names: tuple[str, ...]
    rearm_head: dict | None      # {"features": [...], "coef": [...], "intercept": b}
    platt: dict                  # {"rearm": {"coef": a, "intercept": b} | None, "tick": ...}

    @classmethod
    def load(cls, directory: Path, meta: dict) -> "Bag":
        spec = meta["boosters"]
        boosters = tuple(lgb.Booster(model_file=str(Path(directory) / b["file"])) for b in spec)
        return cls(boosters=boosters, n_trees=tuple(int(b["n_trees"]) for b in spec),
                   feature_names=tuple(meta["feature_names"]),
                   rearm_head=meta.get("rearm_head"), platt=meta["platt"])

    def _col(self, x: np.ndarray, name: str) -> np.ndarray:
        return x[:, self.feature_names.index(name)]

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """Вероятность подтверждения в горизонте — ровно как `run_fold` на обучении."""
        p = np.median([b.predict(x, num_iteration=n)
                       for b, n in zip(self.boosters, self.n_trees)], axis=0)
        rearm = self._col(x, "is_rearm") == 1
        head = self.rearm_head
        if head is not None and rearm.any():
            feats = np.column_stack([self._col(x, f) for f in head["features"]])[rearm]
            z_lr = np.log1p(feats) @ np.asarray(head["coef"], dtype=float) + head["intercept"]
            p_lr = _sigmoid(z_lr)
            p = p.copy()
            p[rearm] = _sigmoid(0.5 * _logit(p[rearm]) + 0.5 * _logit(p_lr))
        out = p.copy()
        for kind, mask in (("rearm", rearm), ("tick", ~rearm)):
            cal = self.platt.get(kind)
            if cal is not None and mask.any():
                out[mask] = _sigmoid(cal["coef"] * _logit(p[mask]) + cal["intercept"])
        return out

    def contributions(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """`(средний логит мешка, средние вклады)`; вклады в сумме с базой дают логит."""
        raw = np.mean([b.predict(x, num_iteration=n, raw_score=True)
                       for b, n in zip(self.boosters, self.n_trees)], axis=0)
        contrib = np.mean([np.asarray(b.predict(x, num_iteration=n, pred_contrib=True))
                           .reshape(x.shape[0], -1)
                           for b, n in zip(self.boosters, self.n_trees)], axis=0)
        return np.asarray(raw, dtype=float).reshape(-1), contrib
