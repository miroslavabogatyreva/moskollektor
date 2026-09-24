import { apiFetch } from '../../lib/api'
import type { AuthInfo } from './types'

// Без сессии — тот же самый apiFetch: 401 сюда не приходит, метод открыт
// (ДОГОВОР API), редирект на /login не сработает, даже если мы уже там.
export async function fetchAuthInfo(): Promise<AuthInfo> {
  const r = await apiFetch('/api/auth/info')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
