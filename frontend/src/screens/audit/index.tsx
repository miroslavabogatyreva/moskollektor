import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../../lib/api'
import { errorMessage } from '../../lib/format'

/* «Журнал действий» — план 5.14 (MOS-124), приёмка НФ-77, Ф-53, US-25.
   Читает GET /api/audit (backend/app/api/audit.py): строку туда кладёт
   промежуточный слой на каждый запрос к API. Отбор по периоду и логину
   делает сервер — в браузер приходит одна страница, а не весь журнал. */

interface AuditRow {
  action_id: number
  occurred_at: string
  method: string
  path: string
  status_code: number
  login: string | null
}

const PAGE_SIZE = 200 // умолчание GET /api/audit

// <input type="datetime-local"> отдаёт время в поясе браузера без пояса;
// new Date() читает его так же, toISOString() отдаёт серверу момент в UTC.
const момент = (v: string) => (v ? new Date(v).toISOString() : '')

const inputStyle =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function AuditScreen(_props: Record<string, unknown>) {
  const [items, setItems] = useState<AuditRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [login, setLogin] = useState('')

  useEffect(() => {
    setError(null)
    const ac = new AbortController()
    const q = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
    if (from) q.set('from', момент(from))
    if (to) q.set('to', момент(to))
    if (login.trim()) q.set('login', login.trim())
    apiFetch(`/api/audit?${q}`, { signal: ac.signal })
      .then(async (r) => {
        if (r.status === 403) return setForbidden(true)
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        const body = (await r.json()) as { total: number; items: AuditRow[] }
        setItems(body.items)
        setTotal(body.total)
      })
      .catch((e) => {
        if (e?.name !== 'AbortError') setError(errorMessage(e))
      })
    return () => ac.abort()
  }, [from, to, login, offset])

  const фильтр = (setter: (v: string) => void) => (e: Event) => {
    setter((e.target as HTMLInputElement).value)
    setOffset(0)
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Журнал действий
      </h1>

      {forbidden && (
        <p style="color:var(--state-error)">Журнал действий доступен только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">Не удалось загрузить журнал: {error}</p>}

      {!forbidden && (
        <>
          <div class="flex flex-wrap items-end gap-4 text-sm" style="color:var(--text-secondary)">
            <label class="flex flex-col gap-1">
              С момента
              <input
                type="datetime-local"
                value={from}
                onInput={фильтр(setFrom)}
                class="px-2 py-1 rounded text-sm"
                style={inputStyle}
              />
            </label>
            <label class="flex flex-col gap-1">
              По момент
              <input
                type="datetime-local"
                value={to}
                onInput={фильтр(setTo)}
                class="px-2 py-1 rounded text-sm"
                style={inputStyle}
              />
            </label>
            <label class="flex flex-col gap-1">
              Логин
              <input
                type="text"
                placeholder="например, dispatcher1"
                value={login}
                onInput={фильтр(setLogin)}
                class="px-2 py-1 rounded text-sm"
                style={inputStyle}
              />
            </label>
            {items && <span class="num">найдено {total}</span>}
          </div>

          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Время', 'Логин', 'Метод', 'Путь', 'Код ответа'].map((h) => (
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
              {items?.map((r) => (
                <tr key={r.action_id} style="border-bottom:1px solid var(--border-subtle)">
                  <td class="px-2 py-2 num">{new Date(r.occurred_at).toLocaleString('ru-RU')}</td>
                  <td class="px-2 py-2">{r.login ?? '—'}</td>
                  <td class="px-2 py-2">{r.method}</td>
                  <td class="px-2 py-2" style="word-break:break-all">
                    {r.path}
                  </td>
                  <td class="px-2 py-2 num">{r.status_code}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
          {items && total > PAGE_SIZE && (
            <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                class="px-2 py-1 rounded disabled:opacity-50"
                style={inputStyle}
              >
                ← Новее
              </button>
              <span class="num">
                {offset + 1}–{Math.min(offset + items.length, total)} из {total}
              </span>
              <button
                type="button"
                disabled={offset + items.length >= total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                class="px-2 py-1 rounded disabled:opacity-50"
                style={inputStyle}
              >
                Старее →
              </button>
            </div>
          )}
        </>
      )}
    </main>
  )
}
