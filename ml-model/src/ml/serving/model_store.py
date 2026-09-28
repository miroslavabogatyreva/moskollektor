"""Загрузка модели с диска и проверка контракта признаков.

Контракт каталога моделей описан в README.md рядом с этим файлом.
Модель не создаётся и не подменяется заглушкой: нет файла — сервер не стартует.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np

from . import v3_bag

# Поставляемые исследовательские веса: путь не зависит от рабочего каталога.
DEFAULT_MODEL_DIR = str(Path(__file__).resolve().parents[3] / "models/lgbm-v3-bag-2026.09.21")

MODEL_FILE = "model.txt"
META_FILE = "model_meta.json"

# Поля, без которых модель нельзя выпускать: их требует GET /model (HLD 6.5) и приёмка.
REQUIRED_META_FIELDS = (
    "model_version",
    "trained_at",
    "feature_schema",
    "feature_names",
    "object_level",
    "directions",
    "train_rows",
    "holdout_precision",
    "holdout_recall",
    "holdout_median_lead_hours",
    "sha256",
)

VALID_OBJECT_LEVELS = ("pfx", "ch", "collector")


class ModelLoadError(RuntimeError):
    """Модель на диске отсутствует или не совпадает со своим описанием."""


@dataclass(frozen=True)
class LoadedModel:
    """Бустер вместе с разобранным описанием. Неизменяем: версия живёт с образом."""

    booster: lgb.Booster | None
    meta: dict
    sha256: str
    model_dir: Path
    bag: v3_bag.Bag | None = None     # модель v3: мешок бустеров вместо одного model.txt

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self.bag is not None:
            return self.bag.predict_proba(x)
        return np.asarray(self.booster.predict(x), dtype=np.float64).reshape(-1)

    def contributions(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """`(логит, вклады с базой в последнем столбце)`; сумма вкладов равна логиту."""
        if self.bag is not None:
            return self.bag.contributions(x)
        raw = np.asarray(self.booster.predict(x, raw_score=True), dtype=np.float64)
        contrib = np.asarray(self.booster.predict(x, pred_contrib=True), dtype=np.float64)
        return raw.reshape(-1), contrib.reshape(x.shape[0], -1)

    @property
    def horizon_h(self) -> int | None:
        value = self.meta.get("horizon_h")
        return int(value) if value is not None else None

    @property
    def explanation_scope(self) -> str:
        return "mean_tree_logit" if self.bag is not None else "model_logit"

    @property
    def explanation_limitation(self) -> str | None:
        if self.bag is None:
            return None
        return ("Factors describe the mean raw tree score only. The final probability "
                "uses the median tree probability, rearm head and Platt calibration; "
                "these factors do not explain the complete ensemble probability.")

    @property
    def feature_names(self) -> list[str]:
        return list(self.meta["feature_names"])

    @property
    def version(self) -> str:
        return str(self.meta["model_version"])


def model_dir_path() -> Path:
    """Каталог модели. ML_MODEL_DIR переопределяет его для тестов и локального прогона."""
    return Path(os.environ.get("ML_MODEL_DIR", DEFAULT_MODEL_DIR))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    # Читаем кусками: model.txt текстовый, но у больших ансамблей это десятки мегабайт.
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_meta(meta: dict, booster: lgb.Booster, digest: str, model_path: Path) -> None:
    """Проверки, которые дешевле сделать один раз на старте, чем ловить в проде."""
    missing = [f for f in REQUIRED_META_FIELDS if f not in meta]
    if missing:
        raise ModelLoadError(
            f"{META_FILE}: нет обязательных полей: {', '.join(missing)}. "
            "Контракт — src/ml/serving/README.md."
        )

    names = meta["feature_names"]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ModelLoadError(f"{META_FILE}: feature_names должен быть списком строк.")
    if len(set(names)) != len(names):
        raise ModelLoadError(f"{META_FILE}: в feature_names есть повторы.")

    if meta["object_level"] not in VALID_OBJECT_LEVELS:
        raise ModelLoadError(
            f"{META_FILE}: object_level='{meta['object_level']}', "
            f"допустимы {VALID_OBJECT_LEVELS}."
        )

    if not isinstance(meta["directions"], list) or not meta["directions"]:
        raise ModelLoadError(f"{META_FILE}: directions должен быть непустым списком.")

    # sha256 в описании — это то, что уедет в pred.run и в протокол приёмки М-02.
    # Расхождение означает, что веса и описание собраны из разных прогонов.
    if str(meta["sha256"]).lower() != digest:
        raise ModelLoadError(
            f"sha256 не совпал: в {META_FILE} '{meta['sha256']}', "
            f"у файла {model_path.name} '{digest}'. Модель и описание из разных прогонов."
        )

    booster_names = list(booster.feature_name() or [])
    if booster_names and booster_names != list(names):
        first = _first_difference(booster_names, list(names))
        raise ModelLoadError(
            f"Порядок признаков в {MODEL_FILE} и в {META_FILE} разный. {first}"
        )

    n_feat = booster.num_feature()
    if n_feat != len(names):
        raise ModelLoadError(
            f"В {MODEL_FILE} {n_feat} признаков, в {META_FILE} feature_names — {len(names)}."
        )


def _first_difference(expected: list[str], got: list[str]) -> str:
    """Человекочитаемое место первого расхождения двух списков имён."""
    if len(expected) != len(got):
        return f"Ожидается {len(expected)} имён, получено {len(got)}."
    for i, (e, g) in enumerate(zip(expected, got)):
        if e != g:
            return f"Первое расхождение на позиции {i}: ожидается '{e}', получено '{g}'."
    return "Списки совпадают."


def _load_bag(directory: Path, meta: dict) -> LoadedModel:
    """Модель v3: бустеры из `meta["boosters"]`, отпечаток — `v3_bag.bag_sha256`."""
    missing = [f for f in REQUIRED_META_FIELDS if f not in meta]
    missing += [f for f in ("boosters", "platt", "horizon_h") if f not in meta]
    if missing:
        raise ModelLoadError(f"{META_FILE}: нет обязательных полей: {', '.join(missing)}.")
    horizon = meta["horizon_h"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ModelLoadError(f"{META_FILE}: horizon_h должен быть положительным целым числом.")
    if not meta["boosters"]:
        raise ModelLoadError(f"{META_FILE}: boosters не может быть пустым.")
    files = [b["file"] for b in meta["boosters"]]
    absent = [f for f in files if not (directory / f).is_file()]
    if absent:
        raise ModelLoadError(f"В {directory} нет бустеров: {', '.join(absent)}.")
    digest = v3_bag.bag_sha256(directory, files)
    if str(meta["sha256"]).lower() != digest:
        raise ModelLoadError(
            f"sha256 мешка не совпал: в {META_FILE} '{meta['sha256']}', у файлов '{digest}'. "
            "Бустеры и описание из разных прогонов.")
    try:
        bag = v3_bag.Bag.load(directory, meta)
    except Exception as exc:
        raise ModelLoadError(f"LightGBM не смог прочитать бустеры {directory}: {exc}") from exc
    for b, name in zip(bag.boosters, files):
        if list(b.feature_name() or []) != list(meta["feature_names"]):
            raise ModelLoadError(f"Порядок признаков в {name} и в {META_FILE} разный.")
    return LoadedModel(booster=None, meta=meta, sha256=digest, model_dir=directory, bag=bag)


def load_model(model_dir: Path | None = None) -> LoadedModel:
    """Загрузить модель или упасть с текстом, из которого ясно, чего не хватает."""
    directory = Path(model_dir) if model_dir is not None else model_dir_path()
    model_path = directory / MODEL_FILE
    meta_path = directory / META_FILE

    if meta_path.is_file():
        try:
            head = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelLoadError(f"{meta_path} не читается как JSON: {exc}") from exc
        if isinstance(head, dict) and head.get("model_format") == v3_bag.FORMAT:
            return _load_bag(directory, head)

    if not model_path.is_file():
        raise ModelLoadError(
            f"Нет файла модели: {model_path}. Агент обучения кладёт сюда booster LightGBM "
            f"в текстовом формате (booster.save_model). Контракт — src/ml/serving/README.md. "
            "Мок-модель не создаётся."
        )
    if not meta_path.is_file():
        raise ModelLoadError(
            f"Нет описания модели: {meta_path}. Поля перечислены в src/ml/serving/README.md."
        )

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelLoadError(f"{meta_path} не читается как JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise ModelLoadError(f"{meta_path}: ожидается объект JSON верхнего уровня.")

    try:
        booster = lgb.Booster(model_file=str(model_path))
    except Exception as exc:  # текст ошибки LightGBM сам по себе не называет файл
        raise ModelLoadError(f"LightGBM не смог прочитать {model_path}: {exc}") from exc

    digest = sha256_of(model_path)
    _check_meta(meta, booster, digest, model_path)
    return LoadedModel(booster=booster, meta=meta, sha256=digest, model_dir=directory)


def feature_order_problem(expected: list[str], got: list[str]) -> str | None:
    """Текст для 422, если feature_names запроса разошлись с порядком обучения.

    Возвращает None, когда списки совпали. Порядок — часть контракта (HLD 6.3):
    молча переставленные колонки дают тихо неверный прогноз.
    """
    if list(expected) == list(got):
        return None

    parts = [_first_difference(expected, got)]
    missing = [n for n in expected if n not in set(got)]
    extra = [n for n in got if n not in set(expected)]
    if missing:
        parts.append(f"Нет в запросе: {', '.join(missing)}.")
    if extra:
        parts.append(f"Лишние в запросе: {', '.join(extra)}.")
    if not missing and not extra:
        parts.append("Состав имён совпадает, различается только порядок.")
    parts.append(f"Порядок обучения: {', '.join(expected)}.")
    return " ".join(parts)
