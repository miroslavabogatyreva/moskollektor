import type { ForecastListResponse } from './types'

export interface ForecastQuery {
  from?: string
  to?: string
  offset?: number
}

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

function ожидаетсяСписок(body: unknown): body is ForecastListResponse {
  return (
    typeof body === 'object' &&
    body !== null &&
    Array.isArray((body as ForecastListResponse).items) &&
    typeof (body as ForecastListResponse).total === 'number'
  )
}

// GET /api/forecasts?from=&to=&offset= — М-06, М-16, Ф-55. Постраничность
// с умолчанием 200 записей на странице (backend/app/api/routes.py) — на
// 425 183 строках без неё браузер вставал.
export async function fetchForecasts(query: ForecastQuery): Promise<ForecastListResponse> {
  const params = new URLSearchParams()
  if (query.from) params.set('from', query.from)
  if (query.to) params.set('to', query.to)
  if (query.offset) params.set('offset', String(query.offset))
  const qs = params.toString()
  const r = await fetch(`/api/forecasts${qs ? `?${qs}` : ''}`, {
    headers: { 'X-User-Login': API_LOGIN },
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const body: unknown = await r.json()
  if (!ожидаетсяСписок(body)) {
    throw new Error('ответ GET /api/forecasts не по форме {total, items}')
  }
  return body
}
