"""Сервер инференса образа `ml`. Порт 8100.

Реализует контракт HLD разд. 6: POST /predict (6.3 → 6.4), GET /model (6.5), GET /health.
Модель берётся из models/v3_collector_20260922 (или ML_MODEL_DIR), запечённого в образ. Модели нет —
процесс не стартует: тихо посчитать прогноз без модели нельзя.
"""

from __future__ import annotations

import logging
import math
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .model_store import LoadedModel, ModelLoadError, feature_order_problem, load_model
from .schemas import (
    ContribCheck,
    Factor,
    HealthInfo,
    ModelInfo,
    PredictRequest,
    PredictResponse,
    Prediction,
)

LOG = logging.getLogger("ml.serving")

TOP_FACTORS = 5
# Порог из HLD 6.4 и требования Ф-08: сумма вкладов и итоговый скоринг расходятся не
# больше чем на 0,01.
CONTRIB_TOLERANCE = 0.01

_STATE: dict[str, LoadedModel] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Загрузить модель до первого запроса. Ошибка загрузки роняет процесс."""
    model = load_model()
    _STATE["model"] = model
    LOG.info(
        "модель загружена: version=%s sha256=%s признаков=%d уровень=%s",
        model.version,
        model.sha256[:12],
        len(model.feature_names),
        model.meta["object_level"],
    )
    yield
    _STATE.clear()


app = FastAPI(
    title="ml inference",
    version="1",
    lifespan=lifespan,
    # Документация даром: контракт можно посмотреть на /docs без нашей базы.
    docs_url="/docs",
)


def current_model() -> LoadedModel:
    model = _STATE.get("model")
    if model is None:  # до lifespan: только в тестах, которые дёргают функции напрямую
        raise ModelLoadError("Модель не загружена: lifespan приложения не отработал.")
    return model


def _fail(code: str, detail: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


@app.exception_handler(ModelLoadError)
async def _model_load_error(request: Request, exc: ModelLoadError) -> JSONResponse:
    return _fail("model_unavailable", str(exc), status=503)


@app.get("/health", response_model=HealthInfo)
def health() -> HealthInfo:
    model = current_model()
    return HealthInfo(
        model_version=model.version,
        model_sha256=model.sha256,
        n_features=len(model.feature_names),
    )


@app.get("/model", response_model=ModelInfo)
def model_info() -> ModelInfo:
    """HLD 6.5: worker дёргает этот метод на старте каждого расчёта и пишет ответ в pred.run."""
    model = current_model()
    meta = model.meta
    return ModelInfo(
        model_version=str(meta["model_version"]),
        trained_at=str(meta["trained_at"]),
        feature_schema=str(meta["feature_schema"]),
        directions=list(meta["directions"]),
        sha256=model.sha256,
        train_rows=int(meta["train_rows"]),
        holdout_precision=meta.get("holdout_precision"),
        holdout_recall=meta.get("holdout_recall"),
        holdout_median_lead_hours=meta.get("holdout_median_lead_hours"),
        object_level=str(meta["object_level"]),
        n_features=len(model.feature_names),
        feature_names=model.feature_names,
        horizon_h=model.horizon_h,
        explanation_scope=model.explanation_scope,
        explains_probability=model.bag is None,
        explanation_limitation=model.explanation_limitation,
    )


def _validate(req: PredictRequest, model: LoadedModel) -> JSONResponse | None:
    """Все проверки входа. Возвращает готовый 422 либо None, если вход годен."""
    meta = model.meta
    expected_schema = str(meta["feature_schema"])
    if req.schema_version != expected_schema:
        return _fail(
            "schema_version_mismatch",
            f"schema_version='{req.schema_version}', модель обучена на "
            f"'{expected_schema}'. Считать прогноз на несовпадающих признаках нельзя "
            "(HLD 6.5).",
        )

    if model.horizon_h is not None and req.horizon_h != model.horizon_h:
        return _fail(
            "horizon_mismatch",
            f"horizon_h={req.horizon_h}, модель обучена на {model.horizon_h} ч. "
            "Изменение поля запроса не изменяет горизонт обученной модели.",
        )

    problem = feature_order_problem(model.feature_names, req.feature_names)
    if problem is not None:
        return _fail("feature_names_mismatch", problem)

    known = set(meta["directions"])
    unknown = [d for d in req.directions if d not in known]
    if unknown:
        return _fail(
            "unknown_direction",
            f"Направления не обучены: {', '.join(unknown)}. "
            f"Модель умеет: {', '.join(sorted(known))}.",
        )
    if not req.directions:
        return _fail("unknown_direction", "Список directions пуст.")

    if len(req.section_ids) != len(req.values):
        return _fail(
            "length_mismatch",
            f"section_ids — {len(req.section_ids)} элементов, values — "
            f"{len(req.values)} строк. Должно совпадать.",
        )
    if not req.values:
        return _fail("empty_batch", "values пуст: нечего прогнозировать.")

    width = len(req.feature_names)
    bad = [i for i, row in enumerate(req.values) if len(row) != width]
    if bad:
        head = ", ".join(str(i) for i in bad[:5])
        return _fail(
            "row_width_mismatch",
            f"В feature_names {width} имён, а строки values другой длины: индексы {head}"
            f"{' и ещё ' + str(len(bad) - 5) if len(bad) > 5 else ''}.",
        )
    return None


def _to_matrix(values: list[list[float | None]]) -> np.ndarray:
    """null → NaN, а не 0 (HLD 6.3). LightGBM обрабатывает пропуски нативно."""
    n_rows = len(values)
    n_cols = len(values[0])
    out = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    for i, row in enumerate(values):
        for j, cell in enumerate(row):
            if cell is not None:
                out[i, j] = cell
    return out


def _top_factors(contrib_row: np.ndarray, names: list[str]) -> list[Factor]:
    """Топ-5 вкладов по модулю (требование Ф-08). Значения — в логитах."""
    feature_part = contrib_row[: len(names)]
    order = np.argsort(-np.abs(feature_part))[:TOP_FACTORS]
    return [Factor(f=names[k], v=round(float(feature_part[k]), 6)) for k in order]


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse | JSONResponse:
    model = current_model()
    problem = _validate(req, model)
    if problem is not None:
        return problem

    names = model.feature_names
    matrix = _to_matrix(req.values)

    proba = model.predict_proba(matrix)
    # raw — логит, с которым обязаны сойтись вклады TreeSHAP; contrib — (n_rows,
    # n_features + 1), последний столбец — базовое значение. У мешка v3 это средние
    # по бустерам (src/ml/serving/v3_bag.py).
    raw, contrib = model.contributions(matrix)

    # Проверяем сумму в объявленном пространстве: для v3 средний логит деревьев.
    # Это НЕ проверка объяснения итоговой вероятности median + rearm + Platt.
    errors = np.abs(contrib.sum(axis=1) - raw)
    max_err = float(np.nanmax(errors)) if errors.size else 0.0
    over = int(np.count_nonzero(errors > CONTRIB_TOLERANCE))
    if over:
        LOG.warning(
            "вклады не сошлись с итогом на %d строках, макс. расхождение %.6f", over, max_err
        )

    factors = [_top_factors(contrib[i], names) for i in range(matrix.shape[0])]
    probability = [
        (float(p) if model.meta.get("model_format") == "local24.bag.v1" else round(float(p), 6))
        if math.isfinite(float(p)) else 0.0 for p in proba
    ]

    # Направление одно (sensor_failure): модель бинарная. Если заказчик пришлёт два,
    # валидация выше уже отсекла необученные.
    predictions = [
        Prediction(
            direction=direction,
            section_ids=list(req.section_ids),
            probability=probability,
            factors=factors,
        )
        for direction in req.directions
    ]

    return PredictResponse(
        model_version=model.version,
        model_sha256=model.sha256,
        run_id=req.run_id,
        horizon_h=model.horizon_h if model.horizon_h is not None else req.horizon_h,
        # degraded=true только если проверка вкладов не прошла: в остальном путь штатный.
        degraded=bool(over),
        predictions=predictions,
        contrib_check=ContribCheck(
            explanation_scope=model.explanation_scope,
            explains_probability=model.bag is None,
            limitation=model.explanation_limitation,
            tolerance=CONTRIB_TOLERANCE,
            max_abs_err=round(max_err, 8),
            rows_over_tolerance=over,
        ),
    )
