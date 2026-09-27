import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../lib/api'
import { errorMessage, formatDateTime } from '../lib/format'
import { usePoll, свежо } from '../lib/poll'

/* Журнал технологических событий участка — план 5.7 (MOS-54), приёмка Ф-89,
   US-12. Пять колонок по форме Приложения 2 ТЗ; отбор, сортировку, окно дат
   и постраничность делает сервер (GET /api/tech-events, backend/app/api/
   tech_events.py). Участок — section_id, а не имя: имя отбирается подстрокой,
   и «Коллектор 797, пикет 1» тянул бы события пикетов 100 и 137.

   Пустые даты — окно по умолчанию: последние сутки выгрузки, его выбирает
   сервер от края данных. Отбор применяется кнопкой «Применить»: каждый запрос
   идёт ~0,1–1 с и пишет строку в журнал действий. Автообновление — от общего
   опроса раз в минуту (poll.ts, НФ-89).

   Два места (Ф-89): карточка участка передаёт sectionId и типы датчиков из
   своих каналов, общий журнал /tech-events (TechEventsScreen ниже) — без
   участка, по всему парку; там «Объект» и «Тип датчика» вводятся текстом. */

interface TechEvent {
  journal_id: number
  read_time: string
  object: string | null
  sensor_kind: string | null
  value_text: string | null
  event_type: 'Предупреждение' | 'Норма'
}

type SortKey = 'read_time' | 'object' | 'sensor_kind' | 'value_text' | 'event_type'

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'read_time', label: 'Время регистрации' },
  { key: 'object', label: 'Объект' },
  { key: 'sensor_kind', label: 'Тип датчика' },
  { key: 'value_text', label: 'Событие датчика' },
  { key: 'event_type', label: 'Тип события' },
]

const PAGE_SIZE = 200 // умолчание GET /api/tech-events
// Опрос номера последнего события ОДС, мс (US-12 сц. 5, Ф-61: не позже 5 с).
const ОПРОС_ОДС_МС = 2_000

interface Отбор {
  from: string
  to: string
  object: string
  sensor_kind: string
  value_text: string
  event_type: string
}

const ПУСТО: Отбор = {
  from: '',
  to: '',
  object: '',
  sensor_kind: '',
  value_text: '',
  event_type: '',
}

const inputStyle =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function TechEventsScreen(_props: Record<string, unknown>) {
  return (
    <main class="p-5 flex flex-col gap-4">
      <TechEventsTable />
    </main>
  )
}

export function TechEventsTable({
  sectionId,
  sensorKinds,
}: {
  sectionId?: number
  sensorKinds?: string[]
}) {
  const [черновик, setЧерновик] = useState<Отбор>(ПУСТО)
  const [отбор, setОтбор] = useState<Отбор>(ПУСТО)
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'read_time',
    dir: 'desc',
  })
  const [offset, setOffset] = useState(0)
  const [авто, setАвто] = useState(true)
  const [items, setItems] = useState<TechEvent[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const { tick } = usePoll()
  // Выключенное автообновление замораживает tick: эффект не перезапускается.
  const [тикТаблицы, setТикТаблицы] = useState(tick)
  useEffect(() => {
    if (авто) setТикТаблицы(tick)
  }, [tick, авто])

  // События ОДС — не позже 5 с (US-12 сц. 5, Ф-61), а общий опрос раз в минуту.
  // Раз в 2 с спрашиваем только номер последнего события ОДС (max по ключу,
  // доли миллисекунды) и перечитываем таблицу, когда он вырос: 2 с опроса
  // плюс чтение таблицы укладываются в 5 с, а тяжёлый запрос журнала не идёт
  // каждые 2 с с каждой вкладки.
  const [тикОдс, setТикОдс] = useState(0)
  useEffect(() => {
    if (!авто) return
    let последний: number | null | undefined
    // Ответ, пришедший после ухода с экрана или выключения автообновления,
    // в состояние не пишем (code/check_stale_fetch.py).
    let отменено = false
    const спросить = () =>
      apiFetch('/api/tech-events/ods-last')
        .then((r) => (r.ok ? (r.json() as Promise<{ last_id: number | null }>) : null))
        .then((b) => {
          if (!b || отменено) return
          if (последний !== undefined && b.last_id !== последний) setТикОдс((n) => n + 1)
          последний = b.last_id
        })
        .catch(() => {})
    спросить()
    const t = setInterval(спросить, ОПРОС_ОДС_МС)
    return () => {
      отменено = true
      clearInterval(t)
    }
  }, [авто])

  useEffect(() => {
    const ac = new AbortController()
    const q = new URLSearchParams({
      sort: sort.key,
      order: sort.dir,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    })
    if (sectionId != null) q.set('section_id', String(sectionId))
    for (const [k, v] of Object.entries(отбор)) if (v.trim()) q.set(k, v.trim())
    apiFetch(`/api/tech-events?${q}`, { signal: ac.signal })
      .then(async (r) => {
        if (!r.ok) {
          const body = (await r.json().catch(() => null)) as { detail?: unknown } | null
          throw new Error(
            typeof body?.detail === 'string' ? body.detail : `${r.status} ${r.statusText}`,
          )
        }
        const body = (await r.json()) as { total: number; items: TechEvent[] }
        setItems(body.items)
        setTotal(body.total)
        setError(null)
        свежо()
      })
      .catch((e) => {
        if (e?.name === 'AbortError') return
        // Строки прошлого окна под ошибкой читались бы как ответ на новое.
        setItems(null)
        setError(errorMessage(e))
      })
    return () => ac.abort()
  }, [sectionId, отбор, sort, offset, тикТаблицы, тикОдс])

  const поле = (k: keyof Отбор) => (e: Event) =>
    setЧерновик((d) => ({ ...d, [k]: (e.target as HTMLInputElement).value }))

  // Сервер принимает from > to и честно отвечает «пусто» — не пускаем такой отбор.
  const датыНаоборот = !!черновик.from && !!черновик.to && черновик.from > черновик.to

  function применить(e: Event) {
    e.preventDefault()
    if (датыНаоборот) return
    setОтбор(черновик)
    setOffset(0)
  }

  function toggleSort(key: SortKey) {
    setSort((s) =>
      s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' },
    )
    setOffset(0)
  }

  return (
    <section aria-labelledby="tech-events-title" class="flex flex-col gap-2">
      <h2 id="tech-events-title" class="text-sm font-semibold" style="color:var(--text-muted)">
        Журнал технологических событий
      </h2>
      <form
        onSubmit={применить}
        class="flex flex-wrap items-end gap-3 text-sm"
        style="color:var(--text-secondary)"
      >
        <label class="flex flex-col gap-1">
          С даты
          <input
            type="date"
            value={черновик.from}
            onInput={поле('from')}
            class="px-2 py-1 rounded text-sm"
            style={inputStyle}
          />
        </label>
        <label class="flex flex-col gap-1">
          По дату
          <input
            type="date"
            value={черновик.to}
            onInput={поле('to')}
            class="px-2 py-1 rounded text-sm"
            style={inputStyle}
          />
        </label>
        <label class="flex flex-col gap-1">
          Объект
          <input
            type="text"
            value={черновик.object}
            onInput={поле('object')}
            placeholder="часть имени"
            class="px-2 py-1 rounded text-sm"
            style={inputStyle}
          />
        </label>
        <label class="flex flex-col gap-1">
          Тип датчика
          {sensorKinds ? (
            <select
              value={черновик.sensor_kind}
              onChange={поле('sensor_kind')}
              class="px-2 py-1 rounded text-sm"
              style={inputStyle}
            >
              <option value="">Все</option>
              {sensorKinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={черновик.sensor_kind}
              onInput={поле('sensor_kind')}
              placeholder="точно, например «Датчик дыма»"
              class="px-2 py-1 rounded text-sm"
              style={inputStyle}
            />
          )}
        </label>
        <label class="flex flex-col gap-1">
          Событие датчика
          <input
            type="text"
            value={черновик.value_text}
            onInput={поле('value_text')}
            placeholder="часть текста"
            class="px-2 py-1 rounded text-sm"
            style={inputStyle}
          />
        </label>
        <label class="flex flex-col gap-1">
          Тип события
          <select
            value={черновик.event_type}
            onChange={поле('event_type')}
            class="px-2 py-1 rounded text-sm"
            style={inputStyle}
          >
            <option value="">Все</option>
            <option value="Предупреждение">Предупреждение</option>
            <option value="Норма">Норма</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={датыНаоборот}
          class="px-3 py-1 rounded text-sm disabled:opacity-50"
          style={inputStyle}
        >
          Применить
        </button>
        <label class="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={авто}
            onChange={(e) => setАвто((e.target as HTMLInputElement).checked)}
          />
          Автообновление
        </label>
      </form>
      {датыНаоборот && (
        <p class="text-xs" style="color:var(--state-warning)">
          Дата начала позже даты конца.
        </p>
      )}
      {!отбор.from && !отбор.to && (
        <p class="text-xs" style="color:var(--text-muted)">
          Без дат — последние сутки выгрузки; окно не длиннее 31 суток.
        </p>
      )}

      {error && <p style="color:var(--state-error)">Не удалось загрузить журнал: {error}</p>}

      <table
        aria-labelledby="tech-events-title"
        class="w-full text-sm"
        style="border-collapse:collapse"
      >
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
          {items?.map((e) => (
            <tr key={e.journal_id} style="border-bottom:1px solid var(--border-subtle)">
              <td class="px-2 py-2 num">{formatDateTime(e.read_time)}</td>
              <td class="px-2 py-2">{e.object ?? '—'}</td>
              <td class="px-2 py-2">{e.sensor_kind ?? '—'}</td>
              <td class="px-2 py-2">{e.value_text ?? '—'}</td>
              <td
                class="px-2 py-2"
                style={e.event_type === 'Предупреждение' ? 'color:var(--state-warning)' : undefined}
              >
                {e.event_type}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {!error && items?.length === 0 && (
        <p style="color:var(--text-muted)">Событий за период нет.</p>
      )}
      {items && total > PAGE_SIZE && (
        <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            class="px-2 py-1 rounded disabled:opacity-50"
            style={inputStyle}
          >
            ← Назад
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
            Дальше →
          </button>
        </div>
      )}
    </section>
  )
}
