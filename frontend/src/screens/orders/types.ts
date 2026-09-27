export { type Direction, DIRECTION_LABEL } from '../../lib/direction'
import type { Direction } from '../../lib/direction'

// Форма строки списка — contracts/examples/orders/order-list.json. Узкая нарочно:
// обоснование и блок прогноза везёт только карточка, GET /api/orders/{id}.
export interface OrderListItem {
  id: number
  object_name: string
  smvu_key: string
  work_type_name: string
  due_at: string // ISO, +03:00
  deadline_hours: number // due_at − reported_at самой заявки
  status: string
  priority_code: string
}

export interface OrderListResponse {
  schema_version: string
  total: number
  items: OrderListItem[]
}

// Вкладка «Неквитированные» — US-04 сц. 5, GET /api/notifications?acked=false
// (backend/app/api/notifications.py::NotificationItem). acked_at пуст, пока
// диспетчер не квитировал событие через POST /api/notifications/{id}/ack.
export interface UnackedNotification {
  id: number
  reported_at: string
  object_name: string | null
  smvu_key: string | null
  probability: number
  horizon_h: number
}

export interface NotificationsResponse {
  total: number
  items: UnackedNotification[]
}

// Коды и названия — db/migrations/010_orders.sql, сид ref.priority. Код '1'
// заявкам расчёта не достаётся никогда (см. комментарий там же), но встречается
// у ручных заявок, поэтому карта на все четыре.
export const PRIORITY_LABEL: Record<string, string> = {
  '1': 'Аварийный',
  '2': 'Высокий',
  '3': 'Средний',
  '4': 'Низкий',
}

// Форма карточки — contracts/examples/orders/order.json, GET /api/orders/{id}.
export interface OrderObject {
  section_id: number
  smvu_key: string
  collector: number
  picket: number
  func_location_id: number
  func_location_code: string
  name: string
  criticality_code: string
  criticality_name: string
  criticality_reason: string
}

export interface OrderWorkType {
  order_type_code: string
  order_type_name: string
  activity_type_code: string
  activity_type_name: string
}

export interface OrderWorkOrder {
  order_no: string
  status: string
  planned_start: string
  planned_finish: string
}

export interface OrderPriority {
  code: string
  name: string
  response_hours: number
}

export interface OrderForecastRef {
  forecast_id: number
  run_id: number
  as_of: string
  direction: Direction
  horizon_h: number
  probability: number
  risk_rank: number
}

export interface OrderDetail {
  schema_version: string
  id: number
  notification_no: string
  notification_kind: string
  status: string
  source_system: string
  subject: string
  reported_at: string
  due_at: string
  // Срок самой заявки, ч: due_at − reported_at. Рядом — норматив priority.response_hours;
  // у заявок до миграции 037 они расходятся (backend/app/api/orders.py).
  deadline_hours: number
  // null у заявки прежнего пути — у неё не было предупреждения модели
  warning_opened_at: string | null
  // Конец окна риска прогноза, а не предсказанный момент отказа (MOS-179)
  risk_window_end: string | null
  // Статус из системы учёта заказчика (эмулятор хелпдеска, MOS-63, Ф-87).
  // null, пока external_status_at не проставлен ingest'ом (app.ingest.order_status) —
  // непустой external_status_at и есть отметка «пришёл извне, не от человека».
  external_status: string | null
  external_status_at: string | null
  external_assignee: string | null
  object: OrderObject
  work_type: OrderWorkType
  work_order: OrderWorkOrder | null
  priority: OrderPriority
  forecast: OrderForecastRef
  reason: string
  created_at: string
  // null у автозаявки — её завёл расчёт, а не человек (moskollektor-44, 17.09.2026)
  created_by: string | null
}
