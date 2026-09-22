import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchForecasts } from './api'
import { DIRECTION_LABEL, type Direction, type ForecastRow } from './types'
import { errorMessage } from '../../lib/format'
import { rowLink, SkipTable } from '../../lib/a11y'

/* Журнал прогнозов — задача 5.4 (MOS-51), постраничность — 4.13 (MOS-117).
   Данные читаются из GET /api/forecasts. Колонки — время, объект, направление,
   вероятность, горизонт: это поля pred.forecast (db/migrations/004_events.sql),
   а не девятиколоночная таблица из Ф-33/Ф-34/Ф-35 — та часть III, у нас её нет
   в согласовании, и под вердикт с причиной в схеме пока нет таблицы.
   Объект показан как section_id: подтягивать smvu_key из sections.json
   незачем для журнала. Клик по строке ведёт на /forecasts/:forecastId
   (ForecastCard, 6.6, MOS-61) — строка это один прогноз, а не участок.
   Объект и направление фильтруются в браузере в пределах текущей страницы —
   сервер фильтрует только по дате, широкого поиска по всему журналу это не даёт. */

type SortKey = 'computed_at' | 'section_id' | 'direction' | 'probability' | 'horizon_h'

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'computed_at', label: 'Время' },
  { key: 'section_id', label: 'Объект' },
  { key: 'direction', label: 'Направление' },
  { key: 'probability', label: 'Вероятность' },
  { key: 'horizon_h', label: 'Горизонт' },
]

const PAGE_SIZE = 200 // умолчание backend/app/api/routes.py::list_forecasts

function сегодня(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function LogScreen(_props: Record<string, unknown>) {
  const [items, setItems] = useState<ForecastRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState(сегодня)
  const [dateTo, setDateTo] = useState(сегодня)
  const [objectQuery, setObjectQuery] = useState('')
  const [direction, setDirection] = useState<Direction | ''>('')
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'computed_at',
    dir: 'desc',
  })

  useEffect(() => {
    setError(null)
    fetchForecasts({ from: dateFrom || undefined, to: dateTo || undefined, offset })
      .then((r) => {
        setItems(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [dateFrom, dateTo, offset])

  function изменитьДату(setter: (v: string) => void, value: string) {
    setter(value)
    setOffset(0)
  }

  const filtered = useMemo(() => {
    if (!items) return []
    return items
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
  }, [items, objectQuery, direction, sort])

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
            onInput={(e) => изменитьДату(setDateFrom, (e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          />
        </label>
        <label class="flex flex-col gap-1">
          По дату
          <input
            type="date"
            value={dateTo}
            onInput={(e) => изменитьДату(setDateTo, (e.target as HTMLInputElement).value)}
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

      <SkipTable targetId="log-table-end" />
      <table class="w-full text-sm" style="border-collapse:collapse">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                aria-sort={
                  sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'
                }
                class="text-left"
                style="border-bottom:1px solid var(--border-subtle)"
              >
                {/* role="button" на th раньше вытеснял неявную роль columnheader —
                    aria-sort определён только для неё, и программа чтения молчала
                    про направление сортировки (нашёл 5f, 22.09.2026). Настоящая
                    button отдаёт Enter и пробел сама, без ручного onKeyDown. */}
                <button
                  type="button"
                  onClick={() => toggleSort(c.key)}
                  class="w-full text-left px-2 py-2 text-xs uppercase tracking-wide cursor-pointer select-none"
                  style="color:var(--text-muted)"
                >
                  {c.label}
                  {sort.key === c.key && (sort.dir === 'asc' ? ' ↑' : ' ↓')}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => (
            <tr
              key={r.forecast_id}
              {...rowLink(() => route(`/forecasts/${r.forecast_id}`))}
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
      <div id="log-table-end" tabindex={-1} />

      {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {items !== null && filtered.length === 0 && (
        <p style="color:var(--text-muted)">Прогнозов за период нет.</p>
      )}

      {items !== null && total > 0 && (
        <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            class="px-2 py-1 rounded disabled:opacity-50"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            ← Раньше
          </button>
          <span>
            {offset + 1}–{Math.min(offset + items.length, total)} из {total}
          </span>
          <button
            type="button"
            disabled={offset + items.length >= total}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            class="px-2 py-1 rounded disabled:opacity-50"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            Позже →
          </button>
        </div>
      )}
    </main>
  )
}
