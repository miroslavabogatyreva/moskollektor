import type { ForecastRow } from './types'

export interface ForecastQuery {
  from?: string
  to?: string
}

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

// GET /api/forecasts?from=&to= — М-16, Ф-55. Заработал 16.09.2026 (MOS-32).
// Известная ловушка на стороне API: from=to=один день отдаёт 0 строк, а широкий
// диапазон (соседние сутки с обеих сторон) — все. Похоже на исключающую границу
// у to. Не обхожу это здесь — сказано оркестратору, чинить на стороне API.
export async function fetchForecasts(query: ForecastQuery): Promise<ForecastRow[]> {
  const params = new URLSearchParams()
  if (query.from) params.set('from', query.from)
  if (query.to) params.set('to', query.to)
  const qs = params.toString()
  const r = await fetch(`/api/forecasts${qs ? `?${qs}` : ''}`, { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
