import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { SENSOR_LEVELS, SensorBadge } from '../../components/SensorBadge'
import { rowLink, SkipTable } from '../../lib/a11y'
import { apiFetch } from '../../lib/api'
import { errorMessage } from '../../lib/format'
import { свежо } from '../../lib/poll'
import {
  главнаяПричина,
  sensorRiskUrl,
  страница,
  type SensorLevel,
  type SensorRiskPage,
  type SensorSummary,
} from '../../lib/sensorRisk'

/* Дашборд по датчикам — SL.5 (MOS-254), эпик MOS-248. Первый режим дашборда;
   второй, «по участкам», остаётся в index.tsx как был: на нём держатся строки
   приёмки М-* и e2e US-01, US-16, US-21, US-23, US-27.

   Два запроса: GET /api/sensor-risk/summary — плитки и «Где риск сосредоточен»,
   GET /api/sensor-risk?limit=50&offset= — страница таблицы. Весь парк одним
   ответом (11 485 каналов) не тянем: таблица постраничная, а плиткам хватает счёта. */

export const НА_СТРАНИЦЕ = 50

// Сводка раз в минуту от общего опроса (НФ-89), как плитки участков.
// enabled false — режим «по участкам»: сводка датчиков там не нужна, хук зовём
// всё равно (хуки не бывают условными), но запроса не шлём.
export function useSensorSummary(synthetic: boolean, tick: number, enabled: boolean) {
  const [summary, setSummary] = useState<SensorSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!enabled) return
    let отменено = false
    apiFetch(`/api/sensor-risk/summary?synthetic=${synthetic ? 1 : 0}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<SensorSummary>
      })
      .then((s) => {
        if (отменено) return
        setSummary(s)
        setError(null)
        // «Обновлено в …» в шапке — по удачному ответу экрана, как у рисков участков.
        свежо()
      })
      .catch((e) => !отменено && setError(errorMessage(e)))
    return () => {
      отменено = true
    }
  }, [synthetic, tick, enabled])
  return { summary, error }
}

// Ссылка на схему с датчиком: переключатель синтетики едет вместе с переходом.
export const наСхему = (q: string, synthetic: boolean) =>
  `/map?${q}${synthetic ? '' : '&synthetic=0'}`

export function SensorTable({ synthetic, tick }: { synthetic: boolean; tick: number }) {
  const [level, setLevel] = useState<SensorLevel | ''>('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<SensorRiskPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Другой отбор — с первой страницы: пятая страница «высокого риска» обычно пуста.
  useEffect(() => setOffset(0), [synthetic, level])
  useEffect(() => {
    let отменено = false
    apiFetch(sensorRiskUrl({ synthetic, level: level || undefined, limit: НА_СТРАНИЦЕ, offset }))
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<SensorRiskPage>
      })
      .then((d) => {
        if (отменено) return
        setData(d)
        setError(null)
      })
      .catch((e) => !отменено && setError(errorMessage(e)))
    return () => {
      отменено = true
    }
  }, [synthetic, level, offset, tick])

  const кнопка =
    'px-3 py-1 rounded text-sm bg-[var(--bg-surface)] enabled:hover:bg-[var(--bg-row-hover)] disabled:opacity-40 disabled:cursor-not-allowed'
  const листать = data && (
    <div class="flex flex-wrap items-center gap-3 text-sm">
      <button
        type="button"
        class={кнопка}
        style="border:1px solid var(--border-strong); color:var(--text-primary)"
        disabled={offset === 0}
        onClick={() => setOffset(Math.max(0, offset - НА_СТРАНИЦЕ))}
      >
        ← предыдущие
      </button>
      <span class="num" data-testid="sensor-page" style="color:var(--text-secondary)">
        {страница(offset, data.items.length, data.total)}
      </span>
      <button
        type="button"
        class={кнопка}
        style="border:1px solid var(--border-strong); color:var(--text-primary)"
        disabled={offset + data.items.length >= data.total}
        onClick={() => setOffset(offset + НА_СТРАНИЦЕ)}
      >
        следующие →
      </button>
    </div>
  )

  return (
    <>
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 mt-2">
        <h2 id="sensor-table" class="text-sm font-semibold">
          Все датчики по риску
        </h2>
        <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
          Уровень
          <select
            class="text-sm px-2 py-1 rounded"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            value={level}
            onChange={(e) => setLevel((e.target as HTMLSelectElement).value as SensorLevel | '')}
          >
            <option value="">все</option>
            {(['high', 'watch', 'normal'] as const).map((l) => (
              <option key={l} value={l}>
                {SENSOR_LEVELS[l].label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p style="color:var(--state-error)">Не удалось загрузить прогноз по датчикам: {error}</p>
      )}
      {!data && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {data && data.items.length === 0 && (
        <p style="color:var(--text-muted)">Датчиков с таким уровнем нет.</p>
      )}
      {data && data.items.length > 0 && (
        <>
          <SkipTable targetId="sensor-table-end" />
          {/* На телефоне таблица шире экрана — листается она, а не страница. */}
          <div class="overflow-x-auto">
            <table
              data-testid="sensor-table"
              class="w-full text-sm"
              style="border-collapse:collapse; min-width:760px"
            >
              <thead>
                <tr>
                  {['Датчик', 'Тип', 'Коллектор, пикет', 'Уровень', 'Балл', 'Главная причина'].map(
                    (h) => (
                      <th
                        key={h}
                        class="text-left px-2 py-2 text-xs uppercase tracking-wide"
                        style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => {
                  const причина = главнаяПричина(s.reasons)
                  return (
                    <tr
                      key={s.channel_id}
                      data-channel-id={s.channel_id}
                      {...rowLink(() => route(наСхему(`channel=${s.channel_id}`, synthetic)))}
                      style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${SENSOR_LEVELS[s.level].border}; cursor:pointer`}
                    >
                      <td class="px-2 py-2">
                        {s.name}{' '}
                        <span class="num" style="color:var(--text-muted)">
                          · {s.channel_id}
                        </span>
                      </td>
                      <td class="px-2 py-2" style="color:var(--text-secondary)">
                        {s.sensor_kind}
                      </td>
                      <td class="px-2 py-2 whitespace-nowrap">
                        {s.collector_name} · ПК{s.picket}
                      </td>
                      <td class="px-2 py-1.5">
                        <SensorBadge level={s.level} />
                      </td>
                      <td class="px-2 py-2 num">{s.score.toFixed(2)}</td>
                      <td class="px-2 py-2">
                        {причина ? (
                          <>
                            {причина.kind === 'plan' && <strong>ППР: </strong>}
                            {причина.text}
                            {причина.kind === 'synthetic' && (
                              <span
                                class="text-xs rounded px-1.5 ml-1"
                                style="border:1px dashed var(--border-strong); color:var(--text-muted)"
                              >
                                синтетика
                              </span>
                            )}
                          </>
                        ) : (
                          <span style="color:var(--text-muted)">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div id="sensor-table-end" tabindex={-1} />
          {листать}
        </>
      )}
    </>
  )
}
