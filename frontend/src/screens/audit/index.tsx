import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../../lib/api'
import { errorMessage, formatDateTime, МОСКВА_СМЕЩЕНИЕ } from '../../lib/format'

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
  details: Record<string, unknown> | null
  login: string | null
}

// Что маршрут сам положил в details (US-23 сц. 5): у PUT /api/settings/{key} —
// старое и новое значение, у блокировки — логин и old/new. Числа сервер отдаёт
// строкой Decimal («0.630»), показываем их как число: «0.63 → 0.64».
const значение = (v: unknown) =>
  typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v) ? String(Number(v)) : JSON.stringify(v)

function подробности(d: Record<string, unknown> | null): string {
  if (!d) return ''
  const { old, new: нов, ...прочее } = d
  const части = Object.entries(прочее).map(([k, v]) => `${k}: ${значение(v)}`)
  if ('old' in d || 'new' in d) части.push(`${значение(old)} → ${значение(нов)}`)
  return части.join(', ')
}

const PAGE_SIZE = 200 // умолчание GET /api/audit

// <input type="datetime-local"> отдаёт время без пояса и с точностью до минуты.
// Читаем его как московское — тем же поясом, что таблица ниже (MOS-121): без
// смещения new Date() взял бы пояс браузера, и во Владивостоке «с 12:30» искало бы
// с 05:30 по Москве. toISOString() отдаёт серверу момент в UTC. Верхняя граница
// включает всю минуту: «с 12:30 по 12:30» должно найти действия 12:30:15–12:30:40
// (НФ-77, ревью c0 27.09.2026).
const поМоскве = (v: string) => new Date(`${v}${МОСКВА_СМЕЩЕНИЕ}`)
const начало = (v: string) => поМоскве(v).toISOString()
const конецМинуты = (v: string) => new Date(поМоскве(v).getTime() + 59_999).toISOString()

interface Отбор {
  from: string
  to: string
  login: string
  // Каждый GET /api/audit сам пишет строку в журнал (main.py): без верхней
  // границы новая строка встаёт первой и сдвигает страницы. Пока «По момент»
  // пуст, подставляем момент, когда отбор применён, — так же сделано
  // в code/check_stable_paging.py.
  снимок: string
}

const новыйОтбор = (from: string, to: string, login: string): Отбор => ({
  from,
  to,
  login: login.trim(),
  снимок: new Date().toISOString(),
})

const inputStyle =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function AuditScreen(_props: Record<string, unknown>) {
  const [items, setItems] = useState<AuditRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Поля — черновик; запрос уходит по «Найти» или Enter, а не на каждую
  // букву: каждый запрос сам пишет строку в журнал.
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [login, setLogin] = useState('')
  const [отбор, setОтбор] = useState<Отбор>(() => новыйОтбор('', '', ''))

  useEffect(() => {
    setError(null)
    const ac = new AbortController()
    const q = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
    if (отбор.from) q.set('from', начало(отбор.from))
    q.set('to', отбор.to ? конецМинуты(отбор.to) : отбор.снимок)
    if (отбор.login) q.set('login', отбор.login)
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
  }, [отбор, offset])

  const поле = (setter: (v: string) => void) => (e: Event) =>
    setter((e.target as HTMLInputElement).value)

  function найти(e: Event) {
    e.preventDefault()
    setОтбор(новыйОтбор(from, to, login))
    setOffset(0)
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)">Журнал действий</h1>

      {forbidden && (
        <p style="color:var(--state-error)">Журнал действий доступен только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">Не удалось загрузить журнал: {error}</p>}

      {!forbidden && (
        <>
          <form
            onSubmit={найти}
            class="flex flex-wrap items-end gap-4 text-sm"
            style="color:var(--text-secondary)"
          >
            <label class="flex flex-col gap-1">
              С момента
              <input
                type="datetime-local"
                value={from}
                onInput={поле(setFrom)}
                class="px-2 py-1 rounded text-sm"
                style={inputStyle}
              />
            </label>
            <label class="flex flex-col gap-1">
              По момент
              <input
                type="datetime-local"
                value={to}
                onInput={поле(setTo)}
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
                onInput={поле(setLogin)}
                class="px-2 py-1 rounded text-sm"
                style={inputStyle}
              />
            </label>
            <button type="submit" class="btn btn-primary">
              Найти
            </button>
            {items && <span class="num">найдено {total}</span>}
          </form>

          <div class="card p-0 overflow-x-auto">
            <table class="w-full text-sm" style="border-collapse:collapse">
              <thead>
                <tr>
                  {['Время', 'Логин', 'Метод', 'Путь', 'Код ответа', 'Подробности'].map((h) => (
                    <th key={h} class="th">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items?.map((r) => (
                  <tr key={r.action_id} style="border-bottom:1px solid var(--border-subtle)">
                    <td class="px-2 py-2 num">{formatDateTime(r.occurred_at, true)}</td>
                    <td class="px-2 py-2">{r.login ?? '—'}</td>
                    <td class="px-2 py-2">{r.method}</td>
                    <td class="px-2 py-2" style="word-break:break-all">
                      {r.path}
                    </td>
                    <td class="px-2 py-2 num">{r.status_code}</td>
                    <td class="px-2 py-2" style="word-break:break-all">
                      {подробности(r.details)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
          {items && total > PAGE_SIZE && (
            <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                class="btn btn-secondary"
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
                class="btn btn-secondary"
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
