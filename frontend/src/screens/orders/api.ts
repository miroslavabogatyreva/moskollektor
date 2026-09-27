import { apiFetch } from '../../lib/api'
import type { NotificationsResponse, OrderDetail, OrderListResponse } from './types'

// До задачи 6.5 (MOS-60) GET /api/orders и GET /api/orders/{id} — заглушка
// MOS-43, отвечает голым []. Пустой массив у списка read (`d.items`) дал бы
// undefined молча, а у карточки [] — истина в JS, и «заявка не найдена»
// не сработала бы вовсе (нашла 76, 17.09.2026). Поэтому форму ответа сверяем
// здесь, в одном месте на обе функции: не тот контракт — это ошибка загрузки,
// а не «заявок нет» и не «заявка не найдена». Настоящий 200 c пустым items —
// после 6.5 и это законное «заявок нет».
function ожидаетсяСписок(body: unknown): body is OrderListResponse {
  return (
    typeof body === 'object' && body !== null && Array.isArray((body as OrderListResponse).items)
  )
}

function ожидаетсяКарточка(body: unknown): body is OrderDetail {
  return typeof body === 'object' && body !== null && 'object' in body && 'forecast' in body
}

// offset — М-06/М-16 (MOS-117): без него метод отдавал журнал заявок целиком,
// и тот же потолок роста, что нашёлся у /api/forecasts, ждал и эту ручку.
export async function fetchOrders(offset = 0): Promise<OrderListResponse> {
  const qs = offset ? `?offset=${offset}` : ''
  const r = await apiFetch(`/api/orders${qs}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: unknown = await r.json()
  if (!ожидаетсяСписок(body)) {
    throw new Error('ответ GET /api/orders не по контракту orders.v1')
  }
  return body
}

// US-04 сц. 5 — вкладка «Неквитированные», backend/app/api/notifications.py.
// offset — та же причина, что у fetchOrders: без него список ограничен
// умолчанием limit=200 (MOS-238, находка проверяющей: на стенде 346 записей,
// вкладка показывала только первые 200).
export async function fetchUnackedNotifications(offset = 0): Promise<NotificationsResponse> {
  const qs = offset ? `&offset=${offset}` : ''
  const r = await apiFetch(`/api/notifications?acked=false${qs}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function ackNotification(id: number): Promise<void> {
  const r = await apiFetch(`/api/notifications/${id}/ack`, { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
}

export async function fetchOrder(orderId: string): Promise<OrderDetail | null> {
  const r = await apiFetch(`/api/orders/${orderId}`)
  // 403 — чужая заявка или id вне области видимости (MOS-107), для экрана то же, что 404.
  if (r.status === 404 || r.status === 403) return null
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: unknown = await r.json()
  if (!ожидаетсяКарточка(body)) {
    throw new Error('ответ GET /api/orders/{id} не по контракту orders.v1')
  }
  return body
}
