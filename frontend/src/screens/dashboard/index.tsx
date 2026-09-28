import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { RiskBadge } from '../../components/RiskBadge'
import { SyntheticToggle } from '../../components/SyntheticToggle'
import { Tile } from '../../components/Tiles'
import { коллекторов, слово } from '../../lib/plural'
import { синтетикаВключена, уровеньИзАдреса } from '../../lib/sensorRisk'
import { rowLink, SkipTable } from '../../lib/a11y'
import { fetchMe } from '../../lib/auth'
import { fetchDataStatus, fetchRisks, fetchSections, fetchWeatherNow } from './api'
import { errorMessage, formatTime, МОСКВА } from '../../lib/format'
import { usePoll, свежо } from '../../lib/poll'
import { имяОбъекта, процент, словоРиска, указатель, цветРиска } from './rows'
import type { SectionRef } from './rows'
import { SensorTable } from './SensorsView'
import type { DataStatus, RiskRow, WeatherNow } from './types'

/* Дашборд рисков — задача 5.2 (MOS-49), переделан 28.09.2026 под диспетчера
   по просьбе Славы: «бессмысленные полотна непонятно чего».

   Сверху полоса «сейчас»: где я (зона видимости), когда (дата и часы по Москве),
   что за окном (живая погода, GET /api/weather/now). Ниже два режима (эпик
   MOS-248, SL.5, MOS-254): по датчикам (SensorsView.tsx, по умолчанию) —
   флажок синтетики (?synthetic=0) и таблица всех датчиков из GET /api/sensor-risk;
   по участкам (?view=sections) — плитка «Участков в расчёте» и список участков
   по риску. Список остаётся целиком: по числу его строк e2e US-16, US-21, US-23,
   US-27 доказывают подрезку по роли.

   Решение Славы 28.09.2026: плитки «Высокий риск», «Наблюдать», «Неквитированные
   уведомления», «Заявки в работе», панели «Где риск сосредоточен» и «Ждут
   квитирования» повторяли сводку смены на главной (/map) — с дашборда убраны.
   Плитка «Данные по состоянию на» тоже: период данных и срез расчёта стоят
   в шапке (Nav.tsx, data-period). */

const участковЧислом = (n: number) =>
  `${n.toLocaleString('ru-RU')} ${слово(n, ['участок', 'участка', 'участков'])}`

export function DashboardScreen({
  view,
  synthetic,
  level,
}: { view?: string; synthetic?: string; level?: string } & Record<string, unknown>) {
  const поУчасткам = view === 'sections'
  const синтетика = синтетикаВключена(synthetic)
  const [rows, setRows] = useState<RiskRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Сколько участков посчитано (US-01 сц. 4) — отдельным запросом: его отказ
  // не гасит список рисков.
  const [status, setStatus] = useState<DataStatus | null>(null)
  /* Справочник участков — чтобы назвать объект словами (MOS-127). Его отказ
     не гасит ни список, ни плитки: без него в столбце «Объект» останется
     номер участка, и таблица работает дальше. */
  const [sections, setSections] = useState<SectionRef[] | null>(null)
  const [weather, setWeather] = useState<WeatherNow | 'нет' | null>(null)
  const [roles, setRoles] = useState<string[] | null>(null)

  // Всё, что меняется, — раз в минуту от общего опроса (НФ-89, MOS-123),
  // справочник участков и роль статичны и грузятся один раз.
  const { tick } = usePoll()
  useEffect(() => {
    // Эффект перезапускается раз в минуту: медленный ответ прошлого тика
    // (435 КБ рисков) не должен лечь поверх ответа нового.
    let отменено = false
    // Риски участков — 435 КБ раз в минуту — нужны только режиму «по участкам».
    if (поУчасткам)
      fetchRisks()
        .then((r) => {
          if (отменено) return
          setRows(r)
          setError(null)
          свежо()
        })
        .catch((e) => !отменено && setError(errorMessage(e)))
    // «посчитано N из M» у плитки участков — только режиму «по участкам».
    if (поУчасткам)
      fetchDataStatus()
        .then((s) => !отменено && setStatus(s))
        .catch(() => !отменено && setStatus(null))
    fetchWeatherNow()
      .then((w) => !отменено && setWeather(w))
      .catch(() => !отменено && setWeather('нет'))
    return () => {
      отменено = true
    }
  }, [tick, поУчасткам])

  useEffect(() => {
    fetchSections()
      .then(setSections)
      .catch(() => setSections([]))
    fetchMe()
      .then((u) => setRoles(u?.roles ?? []))
      .catch(() => setRoles([]))
  }, [])

  const sorted = useMemo(
    () => (rows ? [...rows].sort((a, b) => a.risk_rank - b.risk_rank) : []),
    [rows],
  )

  const имена = useMemo(() => указатель(sections ?? []), [sections])
  // Имя коллектора для таблицы датчиков: в строке GET /api/sensor-risk только collector_id.
  const коллекторы = useMemo(
    () =>
      new Map((sections ?? []).map((s) => [s.collector, s.collector_name ?? String(s.collector)])),
    [sections],
  )

  const stats = useMemo(() => {
    if (!rows || rows.length === 0) return null
    const stale = rows.filter((r) => r.is_stale).length
    const high = rows.filter((r) => r.risk_class === 'high').length
    const horizons = new Set(rows.map((r) => r.horizon_h))
    const horizonLabel =
      horizons.size === 1
        ? `${[...horizons][0]} ч`
        : `${Math.min(...horizons)}–${Math.max(...horizons)} ч`
    const коллекторов = new Set(
      rows.map((r) => имена.get(r.section_id)?.smvu_key.split(':')[0] ?? r.section_id),
    ).size
    return { total: rows.length, stale, high, horizonLabel, коллекторов }
  }, [rows, имена])
  // Решение Славы 28.09.2026: по умолчанию только участки высокого риска,
  // остальные свёрнуты строкой с числом; ?level=all — весь список, как раньше
  // (по числу его строк e2e доказывают подрезку по роли).
  const всеУчастки = level === 'all'
  const видимые = всеУчастки ? sorted : sorted.filter((r) => r.risk_class === 'high')

  return (
    <main class="p-5 flex flex-col gap-4">
      <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 style="font-family:var(--font-display)">Дашборд рисков</h1>
        <NowStrip
          roles={roles}
          коллекторов={поУчасткам ? (stats?.коллекторов ?? null) : null}
          weather={weather}
        />
      </div>

      <ViewSwitch поУчасткам={поУчасткам} />

      {/* Режим по датчикам — только флажок синтетики и таблица: сводка смены
          (высокий риск, наблюдать, неквитированные, заявки) стоит на главной,
          период данных и срез — в шапке (решение Славы 28.09.2026). */}
      {!поУчасткам && (
        <>
          <SyntheticToggle on={синтетика} />
          <SensorTable
            synthetic={синтетика}
            level={уровеньИзАдреса(level)}
            tick={tick}
            коллекторы={коллекторы}
          />
        </>
      )}

      {поУчасткам && (
        <>
          {error && <p style="color:var(--state-error)">Не удалось загрузить риски: {error}</p>}
          {!rows && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

          {stats && (
            // Плитка одна — по ширине содержимого, а не на всю строку.
            <div class="self-start" style="min-width:min(260px,100%)">
              <Tile
                label="Участков в расчёте"
                value={String(stats.total)}
                sub={
                  status
                    ? `посчитано ${status.sections_scored} из ${status.sections_total}`
                    : undefined
                }
                note={`прогноз на ${stats.horizonLabel}`}
                warn={
                  status && status.sections_scored < status.sections_total
                    ? 'посчитаны не все участки: у остальных прошлый прогноз или прогноза нет'
                    : stats.stale > 0
                      ? `у ${stats.stale} расчёт не прошёл, показан прошлый результат`
                      : undefined
                }
              />
            </div>
          )}

          {rows && rows.length === 0 && <p style="color:var(--text-muted)">Рисков нет.</p>}

          {stats && (
            <>
              <h2 id="risk-table" class="card-title mt-2">
                Участки по риску{' '}
                <span class="font-normal" style="color:var(--text-muted)">
                  · высокий риск {stats.high} · низкий {stats.total - stats.high}
                </span>
              </h2>
              {видимые.length > 0 && <SkipTable targetId="dashboard-table-end" />}
              {видимые.length > 0 && (
                <div class="card p-0 overflow-x-auto">
                  <table class="w-full text-sm" style="border-collapse:collapse">
                    <thead>
                      <tr>
                        {['Ранг', 'Объект', 'Риск', 'Вероятность'].map((h) => (
                          <th key={h} class="th">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {видимые.map((r) => (
                        <tr
                          key={r.section_id}
                          {...rowLink(() => route(`/objects/${r.section_id}`))}
                          style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${цветРиска(r.risk_class)}; cursor:pointer`}
                        >
                          <td class="px-2 py-2 num">{r.risk_rank}</td>
                          <td class="px-2 py-2">
                            <span class="font-semibold">
                              {имяОбъекта(имена.get(r.section_id), r.section_id)}
                            </span>{' '}
                            <span style="color:var(--text-muted)" class="num">
                              · {r.section_id}
                            </span>
                          </td>
                          <td class="px-2 py-1.5">
                            <RiskBadge cls={r.risk_class}>{словоРиска(r.risk_class)}</RiskBadge>
                            {r.is_stale && (
                              <span style="color:var(--state-warning)">
                                {' '}
                                · расчёт не прошёл, показан прошлый
                              </span>
                            )}
                          </td>
                          <td class="px-2 py-2 num">{процент(r.probability)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div id="dashboard-table-end" tabindex={-1} />
              {!всеУчастки && (
                <p data-testid="sections-normal" class="flex flex-wrap items-center gap-3 text-sm">
                  <span style="color:var(--text-secondary)">
                    {stats.high === 0
                      ? `Сейчас участков высокого риска нет, без высокого риска ${участковЧислом(stats.total)}`
                      : `Без высокого риска — ${участковЧислом(stats.total - stats.high)}`}
                  </span>
                  <a class="btn btn-secondary" href="/dashboard?view=sections&level=all">
                    Показать
                  </a>
                </p>
              )}
            </>
          )}
        </>
      )}
    </main>
  )
}

// Режимы экрана — ссылками, а не кнопками: режим живёт в адресе (?view=sections),
// его можно прислать ссылкой и открыть на настенном экране сразу нужным.
function ViewSwitch({ поУчасткам }: { поУчасткам: boolean }) {
  const пункты: [string, string, boolean][] = [
    ['По датчикам', '/dashboard', !поУчасткам],
    ['По участкам', '/dashboard?view=sections', поУчасткам],
  ]
  return (
    <nav aria-label="Режим дашборда" class="seg self-start">
      {пункты.map(([имя, href, выбран]) => (
        <a key={href} href={href} aria-current={выбран ? 'page' : undefined}>
          {имя}
        </a>
      ))}
    </nav>
  )
}

// Зона видимости словами роли (Ф-94): ОДС и администратор видят весь парк,
// диспетчер — свой район, техник — свой комплекс. Имени района API не отдаёт,
// поэтому зону меряем числом коллекторов, которые пришли в ответе рисков.
function зона(roles: string[]): string {
  if (roles.includes('ods_dispatcher') || roles.includes('admin')) return 'весь парк'
  if (roles.includes('dispatcher')) return 'ваш район'
  if (roles.includes('technician')) return 'ваш комплекс'
  return 'ваша зона'
}

const день = new Intl.DateTimeFormat('ru-RU', {
  timeZone: МОСКВА,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

/* Полоса «сейчас»: зона, дата и часы по Москве, погода за окном. Часы тикают
   сами раз в 15 секунд — минуты на них не должны отставать от настенных. */
function NowStrip({
  roles,
  коллекторов: n,
  weather,
}: {
  roles: string[] | null
  коллекторов: number | null
  weather: WeatherNow | 'нет' | null
}) {
  const [сейчас, setСейчас] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setСейчас(new Date()), 15_000)
    return () => clearInterval(id)
  }, [])
  return (
    <div data-testid="now-strip" class="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-sm">
      <span>
        <b>Москва</b>
        {roles && (
          <span style="color:var(--text-secondary)">
            {' '}
            · {зона(roles)}
            {n != null && `, ${коллекторов(n)}`}
          </span>
        )}
      </span>
      <span>
        {день.format(сейчас).replace(' г.', '')},{' '}
        <b class="num">{formatTime(сейчас).slice(0, 5)}</b>
      </span>
      <span
        title={
          weather && weather !== 'нет'
            ? `${weather.source}, на ${weather.observed_at.slice(11)}`
            : undefined
        }
      >
        {weather === null ? (
          <span style="color:var(--text-muted)">погода…</span>
        ) : weather === 'нет' ? (
          <span style="color:var(--text-muted)">погода недоступна</span>
        ) : (
          <>
            <b class="num">
              {weather.temp_c > 0 ? '+' : ''}
              {Math.round(weather.temp_c)} °C
            </b>{' '}
            <span style="color:var(--text-secondary)">
              · {weather.sky} · ветер {Math.round(weather.wind_ms)} м/с
              {weather.precip_mm > 0 && ` · осадки ${weather.precip_mm} мм`}
            </span>
          </>
        )}
      </span>
    </div>
  )
}
