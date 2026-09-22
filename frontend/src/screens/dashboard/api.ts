import type { DataStatus, RiskRow } from './types'

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

export async function fetchRisks(): Promise<RiskRow[]> {
  const r = await fetch('/api/risks', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// Край выгрузки спрашиваем у данных, а не выводим из прогнозов (MOS-148).
// Отдельный запрос стоит 5 мс против 435 КБ у /api/risks — плитку можно
// обновлять раз в минуту (НФ-89), не перекачивая список рисков.
export async function fetchDataStatus(): Promise<DataStatus> {
  const r = await fetch('/api/data-status', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
