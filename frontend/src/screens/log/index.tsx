import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchForecasts, fetchOutcomeSummary, type OutcomeSummary } from './api'
import { DIRECTION_LABEL, type Direction, type ForecastRow } from './types'
import { errorMessage, formatDateTime, имяУчастка } from '../../lib/format'
import { rowLink, SkipTable } from '../../lib/a11y'
import { usePoll, свежо } from '../../lib/poll'

/* Журнал прогнозов — задача 5.4 (MOS-51), постраничность — 4.13 (MOS-117).
   Данные читаются из GET /api/forecasts. Колонки — время, объект, направление,
   вероятность, горизонт: это поля pred.forecast (db/migrations/004_events.sql),
   а не девятиколоночная таблица из Ф-33/Ф-34/Ф-35 — та часть III, у нас её нет
   в согласовании, и под вердикт с причиной в схеме пока нет таблицы.
   Объект назван так же, как на остальных экранах — «Коллектор 884, пикет 730»
   (US-14, НФ-71, MOS-243): smvu_key берём из /data/sections.json, того же файла,
   что читают дашборд и схема; до его загрузки — номер участка. Клик по строке ведёт на /forecasts/:forecastId
   (ForecastCard, 6.6, MOS-61) — строка это один прогноз, а не участок.
   Период и участок отбирает сервер (GET /api/forecasts?from=&to=&section_id=, US-11
   сц. 1): число строк сходится с total. Направление — в браузере в пределах страницы.
   Отбор живёт в адресе /log?from=&to=&section=&direction=&offset=: «назад» из карточки
   прогноза возвращает тот же отбор (US-11 сц. 4). */

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

// Отбор из адреса: пустого адреса журнал открывается «за сегодня», как и раньше.
function изАдреса() {
  const q = new URLSearchParams(location.search)
  const участок = Number(q.get('section'))
  return {
    from: q.get('from') ?? сегодня(),
    to: q.get('to') ?? сегодня(),
    section: Number.isInteger(участок) && участок > 0 ? участок : null,
    direction: (q.get('direction') ?? '') as Direction | '',
    offset: Math.max(0, Number(q.get('offset')) || 0),
  }
}

export function LogScreen(_props: Record<string, unknown>) {
  const [старт] = useState(изАдреса)
  const [items, setItems] = useState<ForecastRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(старт.offset)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState(старт.from)
  const [dateTo, setDateTo] = useState(старт.to)
  // section — применённый отбор (уходит в API); sectionText — что набрано в поле.
  const [section, setSection] = useState<number | null>(старт.section)
  const [sectionText, setSectionText] = useState(старт.section ? String(старт.section) : '')
  const [sectionError, setSectionError] = useState<string | null>(null)
  const [direction, setDirection] = useState<Direction | ''>(старт.direction)
  const [сводка, setСводка] = useState<OutcomeSummary | null>(null)
  const [ключи, setКлючи] = useState<Map<number, string>>(new Map())
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'computed_at',
    dir: 'desc',
  })

  // Та же страница перезапрашивается раз в минуту от общего опроса (НФ-89, MOS-123).
  const { tick } = usePoll()
  useEffect(() => {
    // AbortController, а не флажок (как в ForecastCard.tsx/ObjectCard.tsx):
    // здесь недостаточно погасить устаревший ответ в состоянии, запрос ушедшей
    // страницы (или предыдущего окна дат) должен оборваться в сети по-настоящему —
    // MOS-178, приёмка Playwright видит его в devtools как canceled.
    setError(null)
    const ac = new AbortController()
    fetchForecasts(
      {
        from: dateFrom || undefined,
        to: dateTo || undefined,
        section: section ?? undefined,
        offset,
      },
      ac.signal,
    )
      .then((r) => {
        setItems(r.items)
        setTotal(r.total)
        свежо()
      })
      .catch((e) => {
        if (e?.name !== 'AbortError') setError(errorMessage(e))
      })
    return () => ac.abort()
  }, [dateFrom, dateTo, section, offset, tick])

  // Сводка исходов за тот же отбор (US-20): руководитель видит, насколько верить
  // прогнозу, а сумма пяти чисел сходится с «Найдено». Отдельным запросом — счёт
  // по всему периоду дороже страницы, и журнал не ждёт его.
  useEffect(() => {
    const ac = new AbortController()
    setСводка(null)
    fetchOutcomeSummary(
      { from: dateFrom || undefined, to: dateTo || undefined, section: section ?? undefined },
      ac.signal,
    )
      .then(setСводка)
      .catch((e) => {
        if (e?.name !== 'AbortError') console.error('forecast-outcomes:', errorMessage(e))
      })
    return () => ac.abort()
  }, [dateFrom, dateTo, section, tick])

  // Отбор — в адрес, заменой текущей записи истории: «назад» из карточки прогноза
  // приходит на /log с тем же отбором, а не на журнал «за сегодня».
  useEffect(() => {
    const q = new URLSearchParams()
    if (dateFrom) q.set('from', dateFrom)
    if (dateTo) q.set('to', dateTo)
    if (section) q.set('section', String(section))
    if (direction) q.set('direction', direction)
    if (offset) q.set('offset', String(offset))
    history.replaceState(history.state, '', `/log?${q}`)
  }, [dateFrom, dateTo, section, direction, offset])

  useEffect(() => {
    // Справочник статический и один на экран — его не перезапрашивает опрос.
    const ac = new AbortController()
    fetch('/data/sections.json', { signal: ac.signal })
      .then((r) => (r.ok ? (r.json() as Promise<{ section_id: number; smvu_key: string }[]>) : []))
      .then((all) => setКлючи(new Map(all.map((x) => [x.section_id, x.smvu_key]))))
      // Не загрузился — журнал остаётся с номерами участков, а не падает.
      .catch((e) => {
        if (e?.name !== 'AbortError') console.error('sections.json:', errorMessage(e))
      })
    return () => ac.abort()
  }, [])

  const имя = (id: number) => {
    const ключ = ключи.get(id)
    return ключ ? имяУчастка(ключ) : `Участок ${id}`
  }

  function изменитьДату(setter: (v: string) => void, value: string) {
    setter(value)
    setOffset(0)
  }

  // Участок — номер или имя «Коллектор 884, пикет 730», как его пишут все экраны.
  function применитьУчасток(текст: string) {
    const t = текст.trim()
    let id: number | null = null
    if (/^\d+$/.test(t)) id = Number(t)
    else if (t) {
      for (const [sid, ключ] of ключи) if (имяУчастка(ключ) === t) id = sid
      if (id == null) {
        setSectionError('Участок не найден: введите номер или «Коллектор N, пикет M»')
        return
      }
    }
    setSectionError(null)
    setSection(id)
    setOffset(0)
  }

  const filtered = useMemo(() => {
    if (!items) return []
    return items
      .filter((r) => !direction || r.direction === direction)
      .sort((a, b) => {
        const [x, y] = [a[sort.key], b[sort.key]]
        const cmp =
          typeof x === 'number' && typeof y === 'number'
            ? x - y
            : String(x).localeCompare(String(y))
        return sort.dir === 'asc' ? cmp : -cmp
      })
  }, [items, direction, sort, ключи])

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
          Участок
          <input
            type="text"
            placeholder="например, 401 или Коллектор 889, пикет 1"
            value={sectionText}
            aria-describedby={sectionError ? 'log-section-error' : undefined}
            onInput={(e) => setSectionText((e.target as HTMLInputElement).value)}
            onChange={(e) => применитьУчасток((e.target as HTMLInputElement).value)}
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

      {sectionError && (
        <p id="log-section-error" role="status" style="color:var(--state-error)">
          {sectionError}
        </p>
      )}
      {items !== null && (
        <p data-testid="log-total" class="text-sm" style="color:var(--text-secondary)">
          Найдено: {total}
        </p>
      )}
      {сводка && (
        <p
          data-testid="outcome-summary"
          class="text-sm flex flex-wrap gap-x-4"
          style="color:var(--text-secondary)"
        >
          <span>Исходы за период:</span>
          {(
            [
              ['подтвердилось', сводка.confirmed],
              ['ложная', сводка.false_alarm],
              ['не проверяли', сводка.not_checked],
              ['горизонт истёк', сводка.horizon_expired],
              ['ещё открыт', сводка.open],
            ] as const
          ).map(([слово, n], i) => (
            <span key={слово} data-testid={`outcome-${i}`}>
              {слово} <b class="num">{n}</b>
            </span>
          ))}
        </p>
      )}

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
            <th
              class="text-left px-2 py-2 text-xs uppercase tracking-wide"
              style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
            >
              Решение
            </th>
            <th
              class="text-left px-2 py-2 text-xs uppercase tracking-wide"
              style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
            >
              Исход
            </th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => (
            <tr
              key={r.forecast_id}
              data-forecast-id={r.forecast_id}
              data-section-id={r.section_id}
              {...rowLink(() => route(`/forecasts/${r.forecast_id}`))}
              style="border-bottom:1px solid var(--border-subtle); cursor:pointer"
            >
              <td class="px-2 py-2 num">{new Date(r.computed_at).toLocaleString('ru-RU')}</td>
              <td class="px-2 py-2">{имя(r.section_id)}</td>
              <td class="px-2 py-2">{DIRECTION_LABEL[r.direction]}</td>
              <td class="px-2 py-2 num">{r.probability.toFixed(2)}</td>
              <td class="px-2 py-2 num">{r.horizon_h} ч</td>
              {/* Решение диспетчера (US-08, US-09 сц. 4): разобран ли прогноз, кем,
                  когда и проверен ли по внешним источникам. «нет» — словом, а не
                  пустой ячейкой: пустая читается как «не загрузилось». */}
              <td class="px-2 py-2">
                {r.decision ? (
                  <>
                    {r.decision.decision_name}
                    <span style="color:var(--text-muted)">
                      {' '}
                      · {r.decision.decided_by}, {formatDateTime(r.decision.decided_at)}
                      {r.decision.verified_externally && ' · проверено по внешним источникам'}
                    </span>
                  </>
                ) : (
                  <span style="color:var(--text-muted)">нет</span>
                )}
              </td>
              {/* Исход (US-10 сц. 4, 5): отмеченный человеком — словом и причиной;
                  без отметки — «горизонт истёк» или «открыт», система сама его не ставит. */}
              <td class="px-2 py-2" data-testid="outcome-cell">
                {r.outcome ? (
                  <>
                    {r.outcome.outcome_name}
                    {r.outcome.reason_name && ` · ${r.outcome.reason_name}`}
                  </>
                ) : (
                  <span style="color:var(--text-muted)">
                    {r.horizon_expired ? 'горизонт истёк' : 'открыт'}
                  </span>
                )}
              </td>
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
