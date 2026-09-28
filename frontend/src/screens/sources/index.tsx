import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../../lib/api'
import { errorMessage, formatDateTime } from '../../lib/format'

/* «Источники данных» — US-26 (MOS-211), Ф-85, Ф-82. Администратор видит, когда
   пришли последние данные от каждого источника, и замечает остановку раньше,
   чем диспетчер увидит старый прогноз. Отставание и норму считает сервер
   (backend/app/api/sources.py), экран только пишет их словами. Обновляется
   сам раз в 30 секунд — НФ-89 требует не реже раза в минуту. */

interface Source {
  code: string
  name: string
  last_data_at: string | null
  lag_s: number | null
  norm_s: number
  lagging: boolean
}

const ОБНОВЛЕНИЕ_МС = 30_000

function состояние(s: Source): string {
  if (s.lag_s === null) return 'данных не было'
  const мин = Math.floor(s.lag_s / 60)
  return s.lagging ? `отстаёт ${мин} мин` : `в норме, ${мин} мин назад`
}

export function SourcesScreen(_props: Record<string, unknown>) {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = () =>
      apiFetch('/api/sources')
        .then(async (r) => {
          if (r.status === 403) return setForbidden(true)
          if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
          setSources(await r.json())
          setError(null)
        })
        .catch((e) => setError(errorMessage(e)))
    load()
    const t = setInterval(load, ОБНОВЛЕНИЕ_МС)
    return () => clearInterval(t)
  }, [])

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)">Источники данных</h1>

      {forbidden && (
        <p style="color:var(--state-error)">Доступ запрещён: раздел виден только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">{error}</p>}
      {!sources && !forbidden && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {sources && (
        <div class="card p-0 overflow-x-auto">
          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Источник', 'Последние данные', 'Норма', 'Состояние'].map((h) => (
                  <th key={h} class="th">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.code} style="border-bottom:1px solid var(--border-subtle)">
                  <td class="px-2 py-2">{s.name}</td>
                  <td class="px-2 py-2 num" data-testid="last-data">
                    {s.last_data_at ? formatDateTime(s.last_data_at) : '—'}
                  </td>
                  <td class="px-2 py-2 num">не старше {Math.round(s.norm_s / 60)} мин</td>
                  <td class="px-2 py-2" data-testid="state">
                    <span style={`color:var(--state-${s.lagging ? 'error' : 'success'})`}>
                      {состояние(s)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
