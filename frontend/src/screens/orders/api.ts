import type { OrderDetail, OrderListItem, OrderListResponse } from './types'

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

export async function fetchOrders(): Promise<OrderListItem[]> {
  const r = await fetch('/api/orders', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: OrderListResponse = await r.json()
  return body.items
}

export async function fetchOrder(orderId: string): Promise<OrderDetail | null> {
  const r = await fetch(`/api/orders/${orderId}`, { headers: { 'X-User-Login': API_LOGIN } })
  if (r.status === 404) return null
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
