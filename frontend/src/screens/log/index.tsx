import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchForecasts } from './api'
import { DIRECTION_LABEL, type Direction, type ForecastRow } from './types'

/* Журнал прогнозов — задача 5.4 (MOS-51). Данные читаются из GET /api/forecasts
   (заработал 16.09.2026, MOS-32). Колонки — время, объект, направление,
   вероятность, горизонт: это поля pred.forecast (db/migrations/004_events.sql),
   а не девятиколоночная таблица из Ф-33/Ф-34/Ф-35 — та часть III, у нас её нет
   в согласовании, и под вердикт с причиной в схеме пока нет таблицы.
   Объект показан как section_id: подтягивать smvu_key из sections.json
   незачем для журнала. Клик по строке ведёт на /objects/:sectionId
   (ObjectCard, 5.5, MOS-52). */

type SortKey = 'computed_at' | 'section_id' | 'direction' | 'probability' | 'horizon_h'

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'computed_at', label: 'Время' },
  { key: 'section_id', label: 'Объект' },
  { key: 'direction', label: 'Направление' },
  { key: 'probability', label: 'Вероятность' },
  { key: 'horizon_h', label: 'Горизонт' },
]

export function LogScreen(_props: Record<string, unknown>) {
  const [rows, setRows] = useState<ForecastRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [objectQuery, setObjectQuery] = useState('')
  const [direction, setDirection] = useState<Direction | ''>('')
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'computed_at',
    dir: 'desc',
  })

  useEffect(() => {
    setError(null)
    fetchForecasts({ from: dateFrom || undefined, to: dateTo || undefined })
      .then(setRows)
      .catch((e) => setError(String(e)))
  }, [dateFrom, dateTo])

  const filtered = useMemo(() => {
    if (!rows) return []
    return rows
      .filter((r) => !objectQuery || String(r.section_id).includes(objectQuery.trim()))
      .filter((r) => !direction || r.direction === direction)
      .sort((a, b) => {
        const [x, y] = [a[sort.key], b[sort.key]]
        const cmp =
          typeof x === 'number' && typeof y === 'number'
            ? x - y
            : String(x).localeCompare(String(y))
        return sort.dir === 'asc' ? cmp : -cmp
      })
  }, [rows, objectQuery, direction, sort])

  function toggleSort(key: SortKey) {
    setSort((s) =>
      s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' },
    )
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Журнал прогнозов
      </h1>

      {error && <p style="color:var(--state-error)">Не удалось загрузить прогнозы: {error}</p>}

      <div class="flex flex-wrap items-end gap-4 text-sm" style="color:var(--text-secondary)">
        <label class="flex flex-col gap-1">
          С даты
          <input
            type="date"
            value={dateFrom}
            onInput={(e) => setDateFrom((e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          />
        </label>
        <label class="flex flex-col gap-1">
          По дату
          <input
            type="date"
            value={dateTo}
            onInput={(e) => setDateTo((e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          />
        </label>
        <label class="flex flex-col gap-1">
          Объект (section_id)
          <input
            type="text"
            inputMode="numeric"
            placeholder="например, 401"
            value={objectQuery}
            onInput={(e) => setObjectQuery((e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          />
        </label>
        <label class="flex flex-col gap-1">
          Направление
          <select
            value={direction}
            onChange={(e) => setDirection((e.target as HTMLSelectElement).value as Direction | '')}
            class="px-2 py-1 rounded text-sm"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            <option value="">Все</option>
            {(Object.keys(DIRECTION_LABEL) as Direction[]).map((d) => (
              <option key={d} value={d}>
                {DIRECTION_LABEL[d]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <table class="w-full text-sm" style="border-collapse:collapse">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                onClick={() => toggleSort(c.key)}
                class="text-left px-2 py-2 text-xs uppercase tracking-wide cursor-pointer select-none"
                style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
              >
                {c.label}
                {sort.key === c.key && (sort.dir === 'asc' ? ' ↑' : ' ↓')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => (
            <tr
              key={r.forecast_id}
              onClick={() => route(`/objects/${r.section_id}`)}
              style="border-bottom:1px solid var(--border-subtle); cursor:pointer"
            >
              <td class="px-2 py-2 num">{new Date(r.computed_at).toLocaleString('ru-RU')}</td>
              <td class="px-2 py-2 num">{r.section_id}</td>
              <td class="px-2 py-2">{DIRECTION_LABEL[r.direction]}</td>
              <td class="px-2 py-2 num">{r.probability.toFixed(2)}</td>
              <td class="px-2 py-2 num">{r.horizon_h} ч</td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {rows !== null && filtered.length === 0 && (
        <p style="color:var(--text-muted)">Прогнозов за период нет.</p>
      )}
    </main>
  )
}
