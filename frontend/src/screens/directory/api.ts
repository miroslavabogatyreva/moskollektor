import { apiFetch } from '../../lib/api'
import type { DirectoryCheckResult, DirectoryInfo } from './types'

// 403 у не-администратора — законный ответ (settings.read), не поломка
// загрузки: экран решает, что показать, сам, здесь только пробрасываем статус.
export async function fetchDirectory(): Promise<{ status: number; data: DirectoryInfo | null }> {
  const r = await apiFetch('/api/auth/directory')
  if (r.status === 403) return { status: 403, data: null }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return { status: 200, data: await r.json() }
}

export async function checkDirectory(): Promise<DirectoryCheckResult> {
  const r = await apiFetch('/api/auth/directory/check', { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
