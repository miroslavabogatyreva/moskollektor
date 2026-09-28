import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { SENSOR_LEVELS, SensorBadge } from '../../components/SensorBadge'
import { rowLink, SkipTable } from '../../lib/a11y'
import { apiFetch } from '../../lib/api'
import { errorMessage } from '../../lib/format'
import { слово } from '../../lib/plural'
import { свежо } from '../../lib/poll'
import {
  главнаяПричина,
  процент,
  sensorRiskUrl,
  сПараметром,
  страница,
  type SensorLevel,
  type SensorRiskPage,
  type SensorRow,
  type SensorSummary,
} from '../../lib/sensorRisk'

/* Дашборд по датчикам — SL.5 (MOS-254), эпик MOS-248. Первый режим дашборда;
   второй, «по участкам», остаётся в index.tsx: на нём держатся строки
   приёмки М-* и e2e US-01, US-16, US-21, US-23, US-27.

   Запросы: GET /api/sensor-risk?level=high|watch&limit=50&offset= — по странице
   на блок уровня, GET /api/sensor-risk/summary — число нормы для свёрнутой строки
   (сводку зовёт и главная). Весь парк одним ответом (11 485 каналов) не тянем. */

export const НА_СТРАНИЦЕ = 50

// Сводка раз в минуту от общего опроса (НФ-89), как плитки участков.
// enabled false — сводка не нужна: хук зовём всё равно (хуки не бывают
// условными), но запроса не шлём.
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

// Число со словом, разряды через пробел: «11 470 датчиков».
const датчиковЧислом = (n: number) =>
  `${n.toLocaleString('ru-RU')} ${слово(n, ['датчик', 'датчика', 'датчиков'])}`

/* Решение Славы 28.09.2026: дашборд показывает риски, а не полотно нормы.
   Без фильтра — два блока, «Высокий риск · N» и «Наблюдать · N», каждый своим
   запросом GET /api/sensor-risk?level=…, а норма свёрнута строкой с числом из
   сводки и кнопкой «Показать» — она ставит фильтр «Уровень» на «норма».
   С фильтром ?level= — один блок этого уровня (MOS-262). */
export function SensorTable({
  synthetic,
  level,
  tick,
  коллекторы,
}: {
  synthetic: boolean
  // Фильтр из адреса ?level=: читаем при загрузке, пишем при смене (MOS-262).
  level: SensorLevel | ''
  tick: number
  // collector_id → имя из /data/sections.json: в ответе метода имени коллектора нет.
  коллекторы: Map<number, string>
}) {
  // Сводка — только ради числа нормы и пустого случая, нужна лишь без фильтра.
  const { summary } = useSensorSummary(synthetic, tick, level === '')
  // Смена уровня — новая запись истории, в отличие от переключателя синтетики:
  // «Назад» возвращает прежний фильтр, как у фильтров схемы.
  const setLevel = (l: SensorLevel | '') => {
    const { pathname, search } = window.location
    route(сПараметром(pathname, search, 'level', l || null))
  }
  const блок = (l: SensorLevel, скрытьПустой: boolean) => (
    <LevelBlock
      key={l}
      synthetic={synthetic}
      level={l}
      tick={tick}
      коллекторы={коллекторы}
      скрытьПустой={скрытьПустой}
    />
  )

  return (
    <>
      <div
        data-tour="sensor-table-head"
        class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 mt-2"
      >
        <h2 id="sensor-table" class="card-title">
          Датчики с риском
        </h2>
        <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
          Уровень
          <select
            class="input"
            value={level}
            onChange={(e) => setLevel((e.target as HTMLSelectElement).value as SensorLevel | '')}
          >
            <option value="">высокий и наблюдать</option>
            {(['high', 'watch', 'normal'] as const).map((l) => (
              <option key={l} value={l}>
                {SENSOR_LEVELS[l].label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {level ? (
        блок(level, false)
      ) : (
        <>
          {блок('high', true)}
          {блок('watch', true)}
          {summary && (
            <p data-testid="sensor-normal" class="flex flex-wrap items-center gap-3 text-sm">
              <span style="color:var(--text-secondary)">
                {summary.high + summary.watch === 0
                  ? `Сейчас рискованных датчиков нет, в норме ${датчиковЧислом(summary.normal)}`
                  : `В норме — ${датчиковЧислом(summary.normal)}`}
              </span>
              <button type="button" class="btn btn-secondary" onClick={() => setLevel('normal')}>
                Показать
              </button>
            </p>
          )}
        </>
      )}
    </>
  )
}

// Один уровень: заголовок «Высокий риск · N», таблица по 50 строк и листалка.
function LevelBlock({
  synthetic,
  level,
  tick,
  коллекторы,
  скрытьПустой,
}: {
  synthetic: boolean
  level: SensorLevel
  tick: number
  коллекторы: Map<number, string>
  // Без фильтра пустой блок не рисуем: о пустоте скажет строка нормы.
  скрытьПустой: boolean
}) {
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<SensorRiskPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Другой отбор — с первой страницы: пятая страница «высокого риска» обычно пуста.
  useEffect(() => setOffset(0), [synthetic, level])
  useEffect(() => {
    let отменено = false
    apiFetch(sensorRiskUrl({ synthetic, level, limit: НА_СТРАНИЦЕ, offset }))
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

  if (скрытьПустой && data && data.total === 0) return null
  const имя = SENSOR_LEVELS[level].label
  const конец = `sensor-table-end-${level}`
  const кнопка = 'btn btn-secondary'
  return (
    <section data-level-block={level} class="flex flex-col gap-2">
      <h3 class="font-semibold">
        {имя[0].toUpperCase() + имя.slice(1)}
        {data && <span class="num"> · {data.total.toLocaleString('ru-RU')}</span>}
      </h3>
      {error && (
        <p style="color:var(--state-error)">Не удалось загрузить прогноз по датчикам: {error}</p>
      )}
      {!data && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {data && data.items.length === 0 && (
        <p style="color:var(--text-muted)">Датчиков с таким уровнем нет.</p>
      )}
      {data && data.items.length > 0 && (
        <>
          <SkipTable targetId={конец} />
          {/* На телефоне таблица шире экрана — листается она, а не страница. */}
          <div class="card p-0 overflow-x-auto">
            <table
              data-testid="sensor-table"
              class="w-full text-sm"
              style="border-collapse:collapse; min-width:760px"
            >
              <thead>
                <tr>
                  {/* «Уровень» и «Балл» первыми: на 390 px таблица в 760 px листается,
                      и без прокрутки видно только первые колонки (MOS-262). */}
                  {['Уровень', 'Балл', 'Датчик', 'Тип', 'Коллектор, пикет', 'Главная причина'].map(
                    (h) => (
                      <th key={h} class="th">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <SensorRowView
                    key={s.channel_id}
                    s={s}
                    synthetic={synthetic}
                    коллекторы={коллекторы}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div id={конец} tabindex={-1} />
          {/* Листалка — только когда строк больше страницы. */}
          {data.total > НА_СТРАНИЦЕ && (
            <div class="flex flex-wrap items-center gap-3 text-sm">
              <button
                type="button"
                class={кнопка}
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
                disabled={offset + data.items.length >= data.total}
                onClick={() => setOffset(offset + НА_СТРАНИЦЕ)}
              >
                следующие →
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function SensorRowView({
  s,
  synthetic,
  коллекторы,
}: {
  s: SensorRow
  synthetic: boolean
  коллекторы: Map<number, string>
}) {
  const причина = главнаяПричина(s.reasons, s.level)
  return (
    <tr
      data-channel-id={s.channel_id}
      {...rowLink(() => route(наСхему(`channel=${s.channel_id}`, synthetic)))}
      style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${SENSOR_LEVELS[s.level].border}; cursor:pointer`}
    >
      <td class="px-2 py-1.5">
        <SensorBadge level={s.level} />
      </td>
      <td class="px-2 py-2 num">{процент(s.score)}</td>
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
        {s.collector_id == null ? '—' : (коллекторы.get(s.collector_id) ?? String(s.collector_id))}
        {s.picket != null && ` · ПК${s.picket}`}
      </td>
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
}
