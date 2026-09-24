import { apiFetch } from '../../lib/api'
import type { AppUser } from './types'

// 403 у не-администратора — законный ответ (settings.read), не поломка
// загрузки: тот же приём, что и в screens/directory/api.ts.
export async function fetchUsers(): Promise<{ status: number; data: AppUser[] | null }> {
  const r = await apiFetch('/api/auth/users')
  if (r.status === 403) return { status: 403, data: null }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return { status: 200, data: await r.json() }
}

export async function setUserActive(login: string, isActive: boolean): Promise<AppUser> {
  const r = await apiFetch(`/api/auth/users/${encodeURIComponent(login)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  })
  if (!r.ok) {
    const body = (await r.json().catch(() => null)) as { detail?: string } | null
    throw new Error(body?.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json()
}
