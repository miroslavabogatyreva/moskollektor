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
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer

# pydantic-core по умолчанию пишет UTC-дату с суффиксом Z ("...20:59:59Z"),
# а jsonable_encoder (путь без response_model, которым отвечали эти методы
# до сих пор) зовёт datetime.isoformat() и пишет смещение ("...20:59:59+00:00").
# Расхождение поймано именно хешем на стенде 23.09.2026 — без этого type alias
# «ответ не меняется ни на байт» было бы нарушено на каждом поле с датой.
IsoDatetime = Annotated[
    datetime, PlainSerializer(lambda v: v.isoformat(), return_type=str, when_used="json")
]

# Тот же зазор у Decimal: pydantic-core пишет его JSON-строкой ("1.5"), а
# jsonable_encoder — числом через float(). Поймано тем же прогоном хешей на
# avg_duration_h (объекты) и было бы на settings.value, не поймай я его здесь же.
JsonDecimal = Annotated[
    Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")
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


class ForecastList(BaseModel):
    total: int
    items: list[ForecastListItem]


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


class ObjectRecentForecast(BaseModel):
    forecast_id: int
    direction: str
    probability: float
    risk_rank: int
    explanation_ru: str | None
    computed_at: IsoDatetime


class ObjectDetail(BaseModel):
    section_id: int
    smvu_key: str
    inventory_no: str | None
    last_reading_at: IsoDatetime | None
    channels: list[ObjectChannel]
    current_risk: ObjectCurrentRisk | None
    recent_forecasts: list[ObjectRecentForecast]


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
    object: OrderObject
    work_type: OrderWorkType
    work_order: OrderWorkOrder
    priority: OrderPriority
    forecast: OrderForecastRef
    reason: str | None
    created_at: IsoDatetime
    created_by: str | None


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
