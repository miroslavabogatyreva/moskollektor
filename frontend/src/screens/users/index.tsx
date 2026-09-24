import { useEffect, useState } from 'preact/hooks'
import { errorMessage } from '../../lib/format'
import { fetchMe, roleLabels, type AuthUser } from '../../lib/auth'
import { fetchUsers, setUserActive } from './api'
import type { AppUser } from './types'

/* «Пользователи» — MOS-226 (Q4.19), решение Славы 24.09.2026: Ф-66 закрываем
   кнопкой в интерфейсе. Заводят пользователей и роли по-прежнему в каталоге
   AD (US-24) — здесь администратор только блокирует вход существующей
   учётной записи, поэтому у своей строки кнопки нет (не заблокировать себя). */

export function UsersScreen(_props: Record<string, unknown>) {
  const [me, setMe] = useState<AuthUser | null>(null)
  const [users, setUsers] = useState<AppUser[] | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyLogin, setBusyLogin] = useState<string | null>(null)

  useEffect(() => {
    fetchMe()
      .then(setMe)
      .catch(() => setMe(null))
    fetchUsers()
      .then((r) => {
        if (r.status === 403) {
          setForbidden(true)
          return
        }
        setUsers(r.data)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  async function toggle(u: AppUser) {
    setBusyLogin(u.login)
    setError(null)
    try {
      const updated = await setUserActive(u.login, !u.is_active)
      setUsers((prev) => prev?.map((x) => (x.login === updated.login ? updated : x)) ?? prev)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusyLogin(null)
    }
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Пользователи
      </h1>

      {forbidden && (
        <p style="color:var(--state-error)">Доступ запрещён: раздел виден только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">{error}</p>}
      {!users && !forbidden && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {users && (
        <>
          <p style="color:var(--text-muted)" class="text-sm">
            Заводят пользователей и выдают роли в каталоге AD; здесь администратор блокирует вход.
          </p>

          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Логин', 'Имя', 'Источник', 'Роли', 'Состояние', ''].map((h) => (
                  <th
                    key={h}
                    class="text-left px-2 py-2 text-xs uppercase tracking-wide"
                    style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.login} style="border-bottom:1px solid var(--border-subtle)">
                  <td class="px-2 py-2 num">{u.login}</td>
                  <td class="px-2 py-2">{u.full_name}</td>
                  <td class="px-2 py-2">{u.auth_source === 'ldap' ? 'каталог' : 'локальная'}</td>
                  <td class="px-2 py-2">{roleLabels(u.roles)}</td>
                  <td class="px-2 py-2">
                    <span style={`color:var(--state-${u.is_active ? 'success' : 'error'})`}>
                      {u.is_active ? 'активна' : 'заблокирована'}
                    </span>
                  </td>
                  <td class="px-2 py-2">
                    {u.login !== me?.login && (
                      <button
                        type="button"
                        disabled={busyLogin === u.login}
                        onClick={() => toggle(u)}
                        class="px-3 py-1.5 rounded text-sm disabled:opacity-50"
                        style="background:var(--brand-action); color:var(--text-on-brand)"
                      >
                        {u.is_active ? 'Заблокировать' : 'Разблокировать'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  )
}
