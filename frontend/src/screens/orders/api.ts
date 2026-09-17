import type { OrderDetail, OrderListItem, OrderListResponse } from './types'

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

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

export async function fetchOrders(): Promise<OrderListItem[]> {
  const r = await fetch('/api/orders', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: unknown = await r.json()
  if (!ожидаетсяСписок(body)) {
    throw new Error('ответ GET /api/orders не по контракту orders.v1')
  }
  return body.items
}

export async function fetchOrder(orderId: string): Promise<OrderDetail | null> {
  const r = await fetch(`/api/orders/${orderId}`, { headers: { 'X-User-Login': API_LOGIN } })
  if (r.status === 404) return null
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: unknown = await r.json()
  if (!ожидаетсяКарточка(body)) {
    throw new Error('ответ GET /api/orders/{id} не по контракту orders.v1')
  }
  return body
}
