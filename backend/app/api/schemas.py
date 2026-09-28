"""Модели ответов GET-методов API. Задача MOS-221 (Q4.16): condition готовности
блока Q4 «тело проходит по схеме из /openapi.json» и М-17 не проверить без них.

Поля объявлены в том же порядке, в каком их сегодня собирают routes.py/objects.py/
orders.py/audit.py/settings.py: FastAPI сериализует response_model по порядку
ОБЪЯВЛЕНИЯ полей модели, а не по порядку ключей словаря на входе — разъехавшийся
порядок сменил бы байты ответа при том же содержимом (условие задачи — ответ
не меняется ни на байт).

Поле сделано Optional, если столбец в схеме NULLABLE или соединение, из которого
оно взято, — LEFT/LATERAL: строгий тип на таком поле не ловит лишнего, а на первой
же настоящей строке с NULL валит ответ 500 вместо отдачи данных.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PlainSerializer

# pydantic-core по умолчанию пишет UTC-дату с суффиксом Z ("...20:59:59Z"),
# а jsonable_encoder (путь без response_model, которым отвечали эти методы
# до сих пор) зовёт datetime.isoformat() и пишет смещение ("...20:59:59+00:00").
# Расхождение поймано именно хешем на стенде 23.09.2026 — без этого type alias
# «ответ не меняется ни на байт» было бы нарушено на каждом поле с датой.
IsoDatetime = Annotated[
    datetime, PlainSerializer(lambda v: v.isoformat(), return_type=str, when_used="json")
]

# Тот же зазор у Decimal: pydantic-core пишет его JSON-строкой ("1.5"), а
# jsonable_encoder — числом. Первая правка звала float() всегда — нашла 59
# на settings.value=60: jsonable_encoder отдавал число 60 (int), моя модель —
# 60.0. Копия настоящего правила FastAPI (fastapi.encoders.decimal_encoder):
# Decimal без дробной части (exponent >= 0) — int, иначе — float. Показатель
# степени берётся у Decimal, а не по "похоже на целое": Decimal("60.00")
# и Decimal("60") дают разный exponent, и jsonable_encoder различает их так же.
def _decimal_как_jsonable_encoder(v: Decimal) -> int | float:
    exponent = v.as_tuple().exponent
    if isinstance(exponent, int) and exponent >= 0:
        return int(v)
    return float(v)


JsonDecimal = Annotated[
    Decimal, PlainSerializer(_decimal_как_jsonable_encoder, when_used="json")
]


class RiskItem(BaseModel):
    section_id: int
    probability: float
    risk_rank: int
    horizon_h: int
    as_of: IsoDatetime
    is_stale: bool
    risk_class: str | None


class DataStatus(BaseModel):
    data_edge: IsoDatetime | None
    as_of: IsoDatetime | None
    computed_at: IsoDatetime | None
    lag_days: float | None
    sections_scored: int
    sections_total: int


class WeatherStatus(BaseModel):
    observed_at: IsoDatetime | None
    temp_c: float | None
    humidity_pct: float | None
    precip_mm: float | None
    pressure_hpa: float | None
    source: str | None
    fetched_at: IsoDatetime | None
    stale: bool


class WeatherNow(BaseModel):
    """GET /api/weather/now — погода в Москве сейчас, для полосы дашборда."""

    observed_at: str  # час наблюдения по Москве, как отдаёт Open-Meteo: 2026-09-28T10:15
    temp_c: float
    precip_mm: float
    wind_ms: float
    sky: str  # словами по коду WMO: «ясно», «дождь», «снег»…
    source: str


class ForecastListItem(BaseModel):
    forecast_id: int
    section_id: int
    direction: str
    horizon_h: int
    probability: float
    risk_rank: int
    as_of: IsoDatetime
    computed_at: IsoDatetime
    write_reason: str
    # Последнее решение диспетчера (US-08, US-09 сц. 4); null — прогноз не разобран.
    decision: "ForecastDecision | None" = None
    # Исход (US-10): null — никто не отметил; тогда horizon_expired говорит, истёк ли
    # горизонт (computed_at + horizon_h позади). Сама система исход не ставит.
    outcome: "ForecastOutcome | None" = None
    horizon_expired: bool = False


class ForecastList(BaseModel):
    total: int
    items: list[ForecastListItem]


class ForecastDecision(BaseModel):
    feedback_id: int
    decision_code: str
    decision_name: str
    reason_code: str | None
    reason_name: str | None
    comment: str | None
    verified_externally: bool
    decided_by: str
    decided_at: IsoDatetime


class ForecastOutcome(BaseModel):
    outcome_id: int
    outcome_code: str
    outcome_name: str
    reason_code: str | None
    reason_name: str | None
    decided_by: str
    decided_at: IsoDatetime


class OutcomeSummary(BaseModel):
    # US-20: пять исходов за период; каждый прогноз ровно в одном, total — их сумма.
    confirmed: int
    false_alarm: int
    not_checked: int
    horizon_expired: int
    open: int
    total: int


class OutcomeIn(BaseModel):
    # Три исхода (ref.forecast_outcome) и пять причин у «ложной» — Ф-34, Ф-35.
    outcome_code: str
    reason_code: str | None = None


class FeedbackIn(BaseModel):
    decision_code: str
    reason_code: str | None = None
    # Потолок длины — от записки в журнал размером с роман: комментарий читают
    # в карточке и в выгрузке для Николая, 2000 знаков — полстраницы текста.
    comment: str | None = Field(None, max_length=2000)
    # «Проверено по внешним источникам» — шаг 4 сценария ТЗ разд. 12 (HLD разд. 11.6, Ф-91).
    verified_externally: bool = False


class ForecastDetail(BaseModel):
    forecast_id: int
    section_id: int
    direction: str
    horizon_h: int
    probability: float
    risk_rank: int
    factors: list[dict[str, Any]]
    as_of: IsoDatetime
    computed_at: IsoDatetime
    order_ids: list[int]
    # Последнее решение диспетчера (MOS-55, Ф-92); null — прогноз ещё не разобран.
    decision: ForecastDecision | None
    outcome: ForecastOutcome | None = None
    horizon_expired: bool = False


class DictItem(BaseModel):
    code: str
    name: str


class DecisionOptions(BaseModel):
    decisions: list[DictItem]
    reasons: list[DictItem]
    # Исходы прогноза (US-10): подтвердилось, ложная, не проверяли.
    outcomes: list[DictItem] = []


class ObjectChannel(BaseModel):
    channel_id: int
    tag: str
    name: str
    system_kind: str
    sensor_kind: str


class ObjectCurrentRisk(BaseModel):
    run_id: int
    probability: float
    risk_rank: int
    horizon_h: int
    as_of: IsoDatetime
    is_stale: bool
    direction: str | None
    explanation_ru: str | None
    computed_at: IsoDatetime


class ObjectRecentForecast(BaseModel):
    forecast_id: int
    direction: str
    probability: float
    risk_rank: int
    explanation_ru: str | None
    computed_at: IsoDatetime


class DispatcherObject(BaseModel):
    node_id: int
    node_name: str
    collector_id: int
    collector_name: str


class OpenPermit(BaseModel):
    id: int
    number: str
    work_type_name: str
    valid_from: IsoDatetime
    valid_to: IsoDatetime


class ObjectDetail(BaseModel):
    section_id: int
    smvu_key: str
    inventory_no: str | None
    last_reading_at: IsoDatetime | None
    channels: list[ObjectChannel]
    current_risk: ObjectCurrentRisk | None
    recent_forecasts: list[ObjectRecentForecast]
    # Узлы дерева диспетчера, где у участка есть активный канал (MOS-101, 5.9):
    # у 564 участков из 3 173 их больше одного, поэтому список, а не поле.
    dispatcher_objects: list[DispatcherObject]
    # Действующие наряды-допуски (US-13 сц. 1): пусто — участок не в работах.
    open_permits: list[OpenPermit] = []


class TreeNode(BaseModel):
    object_id: int
    name: str
    kind: str
    channels: int
    section_ids: list[int]


class TreeCollector(BaseModel):
    object_id: int
    name: str
    nodes: list[TreeNode]


class ObjectReading(BaseModel):
    read_time: IsoDatetime
    channel_id: int
    is_alarm: bool
    value_text: str | None
    value_num: float | None


class ObjectChannelStat(BaseModel):
    channel_id: int
    system_kind: str
    sensor_kind: str
    name: str
    is_active: bool
    faults_cnt: int
    last_fault_at: IsoDatetime | None
    avg_duration_h: JsonDecimal | None


class ObjectChannelList(BaseModel):
    total: int
    items: list[ObjectChannelStat]


class ChannelEpisodeChannel(BaseModel):
    channel_id: int
    name: str
    sensor_kind: str | None
    system_kind: str | None


class ChannelEpisode(BaseModel):
    started_at: IsoDatetime
    ended_at: IsoDatetime | None
    duration_h: float | None
    fault_value: str


class ChannelEpisodeList(BaseModel):
    channel: ChannelEpisodeChannel
    total: int
    items: list[ChannelEpisode]


class OrderListItem(BaseModel):
    id: int
    object_name: str
    smvu_key: str
    work_type_name: str
    due_at: IsoDatetime | None
    deadline_hours: float
    status: str
    priority_code: str


class OrderList(BaseModel):
    schema_version: str
    total: int
    items: list[OrderListItem]


class OrderObject(BaseModel):
    section_id: int
    smvu_key: str
    collector: int
    picket: int
    func_location_id: int
    func_location_code: str
    name: str
    criticality_code: str | None
    criticality_name: str | None
    criticality_reason: str


class OrderWorkType(BaseModel):
    order_type_code: str
    order_type_name: str
    activity_type_code: str
    activity_type_name: str


class OrderWorkOrder(BaseModel):
    order_no: str
    status: str
    planned_start: IsoDatetime | None
    planned_finish: IsoDatetime | None


class OrderPriority(BaseModel):
    code: str
    name: str
    response_hours: int


class OrderForecastRef(BaseModel):
    forecast_id: int
    run_id: int
    as_of: IsoDatetime
    direction: str
    horizon_h: int
    probability: float
    risk_rank: int


class OrderStatusEntry(BaseModel):
    """Строка истории заявки, maint.notification_status_log (миграция 058, US-19)."""

    status: str
    assignee: str | None
    changed_at: IsoDatetime  # момент смены в источнике
    source: str  # order_system — из системы учёта; user — проставлен человеком


class OrderDetail(BaseModel):
    schema_version: str
    id: int
    notification_no: str
    notification_kind: str
    status: str
    source_system: str
    subject: str
    reported_at: IsoDatetime
    due_at: IsoDatetime | None
    deadline_hours: float
    warning_opened_at: IsoDatetime | None
    risk_window_end: IsoDatetime | None
    external_status: str | None
    external_status_at: IsoDatetime | None
    external_assignee: str | None
    object: OrderObject
    work_type: OrderWorkType
    work_order: OrderWorkOrder
    priority: OrderPriority
    forecast: OrderForecastRef
    reason: str | None
    created_at: IsoDatetime
    created_by: str | None
    status_history: list[OrderStatusEntry]


class AuditItem(BaseModel):
    action_id: int
    occurred_at: IsoDatetime
    method: str
    path: str
    status_code: int
    details: dict[str, Any] | None
    login: str | None
    full_name: str | None


class AuditList(BaseModel):
    total: int
    items: list[AuditItem]


class SettingItem(BaseModel):
    key: str
    value: JsonDecimal
    unit: str | None
    changed_by: int | None
    changed_at: IsoDatetime


class UserItem(BaseModel):
    login: str
    full_name: str
    auth_source: str
    is_active: bool
    roles: list[str]
    has_password: bool  # хранит ли сервис хеш пароля; у auth_source=ldap всегда false (US-24)


class SensorRiskReason(BaseModel):
    text: str
    weight: float
    kind: Literal["real", "synthetic", "plan"]


class SensorEquipment(BaseModel):
    equipment_no: str
    manufacturer: str | None
    model_no: str | None
    in_service_from: date | None
    service_life_years: int | None
    last_check_at: date | None
    last_check_ok: bool | None


class SensorRiskItem(BaseModel):
    channel_id: int
    name: str | None
    sensor_kind: str | None
    picket: int | None
    section_id: int | None
    node_id: int | None
    collector_id: int | None
    score: float
    level: Literal["high", "watch", "normal"]
    reasons: list[SensorRiskReason]
    equipment: SensorEquipment | None  # null при synthetic=false: паспорт синтетический


class SensorRisk(BaseModel):
    synthetic: bool  # эхо параметра: true — балл с синтетическим паспортом
    as_of: IsoDatetime | None  # срез pred.sensor_risk; null — тик ещё не считал
    node: int | None
    node_name: str | None
    collector: int | None
    collector_name: str | None
    total: int
    limit: int
    offset: int
    items: list[SensorRiskItem]


class SensorCollectorHigh(BaseModel):
    collector_id: int
    name: str
    high: int


class SensorRiskSummary(BaseModel):
    synthetic: bool
    as_of: IsoDatetime | None
    high: int
    watch: int
    normal: int
    collectors_with_high: int
    top_collectors: list[SensorCollectorHigh]
