import { apiFetch } from './api'

// Форма ответа login/me — ДОГОВОР API MOS-39. roles — коды из ref.user_role
// (docs/HLD.md разд. 3.4: dispatcher, ods_dispatcher, technician, admin).
export interface AuthUser {
  login: string
  full_name: string
  roles: string[]
  auth_source: 'ldap' | 'local'
}

// Слова заказчика (docs/meetings/2026-09-17-эксперты.md, приёмка Ф-66) — те же,
// что backend кладёт в demo_accounts.role_name (GET /api/auth/info), держим
// здесь единым местом, а не второй раз в каждом экране.
export const ROLE_LABELS: Record<string, string> = {
  dispatcher: 'диспетчер',
  ods_dispatcher: 'диспетчер ОДС',
  technician: 'техник',
  admin: 'администратор ИС',
}

export function roleLabels(roles: string[]): string {
  return roles.map((r) => ROLE_LABELS[r] ?? r).join(', ')
}

export async function fetchMe(): Promise<AuthUser | null> {
  const r = await apiFetch('/api/auth/me')
  if (r.status === 401) return null
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function login(loginValue: string, password: string): Promise<AuthUser> {
  const r = await apiFetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login: loginValue, password }),
  })
  if (!r.ok) {
    const body = (await r.json().catch(() => null)) as { detail?: string } | null
    throw new Error(body?.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export async function logout(): Promise<void> {
  await apiFetch('/api/auth/logout', { method: 'POST' })
}
