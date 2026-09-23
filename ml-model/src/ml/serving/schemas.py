"""Схемы запроса и ответа. Форма — из HLD разд. 6.3 и 6.4, слово в слово."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# protected_namespaces=(): в ответе есть model_version и model_sha256, а pydantic по
# умолчанию резервирует префикс model_ под свои поля.
_CFG = ConfigDict(protected_namespaces=())


class PredictRequest(BaseModel):
    """Вход POST /predict.

    extra='allow': заказчик может добавить поле в свой worker раньше, чем мы обновим
    образ. Лишнее поле не повод ронять расчёт; недостающее — повод, и оно ловится ниже.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="allow")

    schema_version: str
    run_id: int
    computed_at: str | None = None
    horizon_h: int = Field(gt=0)
    directions: list[str]
    feature_names: list[str]
    section_ids: list[int]
    # None означает «признака нет», а не ноль (HLD 6.3). Дальше он станет NaN.
    values: list[list[float | None]]


class Factor(BaseModel):
    """Один вклад признака. v — в логитах, не в процентах (ловушка из HLD 6.4)."""

    model_config = _CFG

    f: str
    v: float


class Prediction(BaseModel):
    model_config = _CFG

    direction: str
    section_ids: list[int]
    probability: list[float]
    factors: list[list[Factor]]


class ContribCheck(BaseModel):
    """Проверка суммы вкладов в указанном пространстве explanation_scope.

    У v3 это средний логит деревьев, не итоговая вероятность ансамбля.

    Поля сверх HLD 6.4. Добавлены, чтобы приёмка читала результат проверки, а не верила
    на слово. Лишние ключи JSON worker игнорирует.
    """

    model_config = _CFG

    explanation_scope: str
    explains_probability: bool
    limitation: str | None = None
    tolerance: float = 0.01
    max_abs_err: float
    rows_over_tolerance: int


class PredictResponse(BaseModel):
    model_config = _CFG

    schema_version: str = "pred.v1"
    model_version: str
    model_sha256: str
    run_id: int
    horizon_h: int
    degraded: bool = False
    predictions: list[Prediction]
    contrib_check: ContribCheck


class ModelInfo(BaseModel):
    """Ответ GET /model (HLD 6.5) плюс поля, нужные нашей приёмке."""

    model_config = _CFG

    model_version: str
    trained_at: str
    feature_schema: str
    directions: list[str]
    sha256: str
    train_rows: int
    holdout_precision: float | None
    holdout_recall: float | None
    # Сверх HLD 6.5:
    holdout_median_lead_hours: float | None = None
    object_level: str
    n_features: int
    feature_names: list[str]
    horizon_h: int | None = None
    explanation_scope: str
    explains_probability: bool
    explanation_limitation: str | None = None


class HealthInfo(BaseModel):
    model_config = _CFG

    status: str = "ok"
    model_version: str
    model_sha256: str
    n_features: int


class ErrorBody(BaseModel):
    """Тело 422. detail — текст, из которого видно, где именно расхождение."""

    model_config = _CFG

    error: str = Field(description="Короткий код ошибки")
    detail: str
