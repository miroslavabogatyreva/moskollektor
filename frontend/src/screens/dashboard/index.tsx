import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { RiskBadge } from '../../components/RiskBadge'
import { rowLink, SkipTable } from '../../lib/a11y'
import { fetchMe } from '../../lib/auth'
import {
  fetchDataStatus,
  fetchOrdersSummary,
  fetchRisks,
  fetchSections,
  fetchUnacked,
  fetchWeatherNow,
} from './api'
import { errorMessage, formatDate, formatDateTime, formatTime, МОСКВА } from '../../lib/format'
import { usePoll, свежо } from '../../lib/poll'
import { отставание } from './lag'
import { имяОбъекта, очаги, процент, словоРиска, указатель, цветРиска } from './rows'
import type { SectionRef } from './rows'
import type { DataStatus, OrdersSummary, RiskRow, Unacked, WeatherNow } from './types'

/* Дашборд рисков — задача 5.2 (MOS-49), переделан 28.09.2026 под диспетчера
   по просьбе Славы: «бессмысленные полотна непонятно чего».

   Сверху вниз — в порядке вопросов, с которыми диспетчер приходит на смену:
   1. Полоса «сейчас»: где я (зона видимости), когда (дата и часы по Москве),
      что за окном (живая погода, GET /api/weather/now).
   2. Плитки: сколько участков высокого риска, сколько неквитированных
      уведомлений, сколько заявок просрочено, всё ли посчитано, по какой дате
      данные. Служебные «устаревших расчётов» и «горизонт» ушли подписями
      в плитку «Участков в расчёте»: устаревшие показываем, только когда они есть.
   3. «Где риск сосредоточен» — коллекторы по числу участков высокого риска,
      ссылка на схему; рядом — пять свежих неквитированных уведомлений.
   4. Полный список участков по риску — ниже. Он остаётся целиком: по числу его
      строк e2e-тесты US-16, US-21, US-23, US-27 доказывают подрезку по роли.

   Каждый запрос со своей ошибкой: отказ погоды или заявок не гасит риски. */

export function DashboardScreen(_props: Record<string, unknown>) {
  const [rows, setRows] = useState<RiskRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  /* Состояние данных отдельным запросом и отдельной ошибкой: край выгрузки
     не выводится из прогнозов (MOS-148), и его недоступность не должна гасить
     список рисков — это две разные беды, и на экране они выглядят по-разному. */
  const [status, setStatus] = useState<DataStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  /* Справочник участков — чтобы назвать объект словами (MOS-127). Его отказ
     не гасит ни список, ни плитки: без него в столбце «Объект» останется
     номер участка, и таблица работает дальше. */
  const [sections, setSections] = useState<SectionRef[] | null>(null)
  const [weather, setWeather] = useState<WeatherNow | 'нет' | null>(null)
  const [unacked, setUnacked] = useState<Unacked | null>(null)
  const [orders, setOrders] = useState<OrdersSummary | null>(null)
  const [roles, setRoles] = useState<string[] | null>(null)

  // Всё, что меняется, — раз в минуту от общего опроса (НФ-89, MOS-123),
  // справочник участков и роль статичны и грузятся один раз.
  const { tick } = usePoll()
  useEffect(() => {
    // Эффект перезапускается раз в минуту: медленный ответ прошлого тика
    // (435 КБ рисков) не должен лечь поверх ответа нового.
    let отменено = false
    fetchRisks()
      .then((r) => {
        if (отменено) return
        setRows(r)
        setError(null)
        свежо()
      })
      .catch((e) => !отменено && setError(errorMessage(e)))
    fetchDataStatus()
      .then((s) => {
        if (отменено) return
        setStatus(s)
        setStatusError(null)
      })
      .catch((e) => !отменено && setStatusError(errorMessage(e)))
    fetchWeatherNow()
      .then((w) => !отменено && setWeather(w))
      .catch(() => !отменено && setWeather('нет'))
    fetchUnacked()
      .then((u) => !отменено && setUnacked(u))
      .catch(() => {})
    fetchOrdersSummary()
      .then((o) => !отменено && setOrders(o))
      .catch(() => {})
    return () => {
      отменено = true
    }
  }, [tick])

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
  const горячие = useMemo(() => (rows ? очаги(rows, имена) : []), [rows, имена])

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

  return (
    <main class="p-5 flex flex-col gap-4">
      <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
          Дашборд рисков
        </h1>
        <NowStrip roles={roles} коллекторов={stats?.коллекторов ?? null} weather={weather} />
      </div>

      {error && <p style="color:var(--state-error)">Не удалось загрузить риски: {error}</p>}
      {!rows && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {stats && (
        <div class="grid gap-3" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr))">
          <Tile
            label="Высокий риск"
            value={String(stats.high)}
            sub={
              горячие.length > 0
                ? `на ${коллекторах(горячие.length)}, всего участков ${stats.total}`
                : `всего участков ${stats.total}`
            }
            accent={stats.high > 0 ? 'var(--risk-critical-border)' : undefined}
            href="#risk-table"
          />
          <Tile
            label="Неквитированные уведомления"
            value={unacked ? String(unacked.total) : '…'}
            sub={
              unacked?.items[0]
                ? `последнее ${formatDateTime(unacked.items[0].reported_at)}`
                : unacked
                  ? 'все квитированы'
                  : undefined
            }
            accent={unacked && unacked.total > 0 ? 'var(--state-warning)' : undefined}
            href="/orders"
          />
          <Tile
            label="Заявки в работе"
            value={orders ? String(orders.open) : '…'}
            sub={orders ? (orders.overdue > 0 ? undefined : 'просроченных нет') : undefined}
            warn={orders && orders.overdue > 0 ? `просрочено ${orders.overdue}` : undefined}
            href="/orders"
          />
          <Tile
            label="Участков в расчёте"
            value={String(stats.total)}
            sub={
              status ? `посчитано ${status.sections_scored} из ${status.sections_total}` : undefined
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
          <DataEdgeTile status={status} error={statusError} />
        </div>
      )}

      {stats && (
        <div class="grid gap-3" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr))">
          <Panel title="Где риск сосредоточен">
            {горячие.length === 0 ? (
              <p class="text-sm" style="color:var(--text-muted)">
                Участков высокого риска нет.
              </p>
            ) : (
              <ol class="flex flex-col gap-2 text-sm">
                {горячие.slice(0, 6).map((о) => (
                  <li key={о.коллектор}>
                    <a
                      href={`/map?section=${о.top}`}
                      class="flex items-center gap-3"
                      style="color:inherit; text-decoration:none"
                    >
                      <span class="w-32 shrink-0">Коллектор {о.коллектор}</span>
                      <span
                        class="flex-1 h-2 rounded"
                        style="background:var(--bg-table-alt)"
                        aria-hidden="true"
                      >
                        <span
                          class="block h-2 rounded"
                          style={`width:${Math.max(4, (о.высоких / горячие[0].высоких) * 100)}%; background:var(--risk-critical-border)`}
                        />
                      </span>
                      <span class="num w-44 shrink-0 text-right">
                        {о.высоких === о.всего ? `все ${о.всего}` : `${о.высоких} из ${о.всего}`} ·
                        до {процент(о.максимум)}
                      </span>
                    </a>
                  </li>
                ))}
              </ol>
            )}
            {горячие.length > 6 && (
              <p class="text-xs mt-2" style="color:var(--text-muted)">
                и ещё {коллекторах(горячие.length - 6)} — в списке ниже
              </p>
            )}
          </Panel>
          <Panel title="Ждут квитирования">
            {!unacked ? (
              <p class="text-sm" style="color:var(--text-muted)">
                Загрузка…
              </p>
            ) : unacked.items.length === 0 ? (
              <p class="text-sm" style="color:var(--text-muted)">
                Неквитированных уведомлений нет.
              </p>
            ) : (
              <ul class="flex flex-col gap-2 text-sm">
                {unacked.items.map((n) => (
                  <li key={n.id}>
                    <a
                      href={`/objects/${n.section_id}`}
                      class="flex justify-between gap-3"
                      style="color:inherit; text-decoration:none"
                    >
                      <span>{n.object_name}</span>
                      <span class="num shrink-0" style="color:var(--text-secondary)">
                        {процент(n.probability)} за {n.horizon_h} ч ·{' '}
                        {formatDateTime(n.reported_at)}
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      )}

      {rows && rows.length === 0 && <p style="color:var(--text-muted)">Рисков нет.</p>}

      {sorted.length > 0 && stats && (
        <>
          <h2 id="risk-table" class="text-sm font-semibold mt-2">
            Все участки по риску{' '}
            <span class="font-normal" style="color:var(--text-muted)">
              · высокий риск {stats.high} · низкий {stats.total - stats.high}
            </span>
          </h2>
          <SkipTable targetId="dashboard-table-end" />
          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Ранг', 'Объект', 'Риск', 'Вероятность'].map((h) => (
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
              {sorted.map((r) => (
                <tr
                  key={r.section_id}
                  {...rowLink(() => route(`/objects/${r.section_id}`))}
                  style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${цветРиска(r.risk_class)}; cursor:pointer`}
                >
                  <td class="px-2 py-2 num">{r.risk_rank}</td>
                  <td class="px-2 py-2">
                    {имяОбъекта(имена.get(r.section_id), r.section_id)}{' '}
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
          <div id="dashboard-table-end" tabindex={-1} />
        </>
      )}
    </main>
  )
}

const правила = new Intl.PluralRules('ru-RU')
const склонить = (n: number, [one, few, many]: [string, string, string]) => {
  const форма = правила.select(n)
  return `${n} ${форма === 'one' ? one : форма === 'few' ? few : many}`
}
const коллекторах = (n: number) => склонить(n, ['коллекторе', 'коллекторах', 'коллекторах'])
const коллекторов = (n: number) => склонить(n, ['коллектор', 'коллектора', 'коллекторов'])

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

function Panel({ title, children }: { title: string; children: preact.ComponentChildren }) {
  return (
    <section
      class="p-3 rounded flex flex-col gap-2"
      style="background:var(--bg-surface); border:1px solid var(--border-subtle)"
    >
      <h2 class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {title}
      </h2>
      {children}
    </section>
  )
}

/* Край выгрузки и момент расчёта — два разных числа под двумя разными подписями
   (MOS-148). Раньше здесь стояло одно: дашборд брал max(as_of) по строкам ответа
   и подписывал его концом выгрузки. Пока срез назначает планировщик, эти числа
   совпадают; прогон, запущенный руками с другим срезом, показывал на этой плитке
   дату, которой в данных нет — снято на стенде 22.09.2026, прогон 501. */
function DataEdgeTile({ status, error }: { status: DataStatus | null; error: string | null }) {
  if (error) {
    return (
      <Tile
        label="Данные по состоянию на"
        value="—"
        sub={`не удалось спросить у сервера: ${error}`}
      />
    )
  }
  if (!status) {
    return <Tile label="Данные по состоянию на" value="…" sub="спрашиваем сервер" />
  }
  if (!status.data_edge) {
    /* Пустое место здесь читается как ноль, поэтому говорим словами. */
    return (
      <Tile
        label="Данные по состоянию на"
        value="неизвестно"
        sub="суточная свёртка пуста, краю выгрузки взяться неоткуда"
      />
    )
  }
  /* Отставание среза от края — MOS-129. Сервер считает его сам (`lag_days`),
     браузер только подписывает: вычитание двух дат здесь пошло бы в поясе
     того, кто смотрит. Когда срез и край сошлись — а на плановом прогоне они
     сходятся всегда — про срез молчим: две даты под одной плиткой диспетчер
     читает как одну, и лишняя строка «отставание 0» перестаёт замечаться
     ровно тогда, когда в ней появляется число. */
  const разрыв = отставание(status.lag_days)
  return (
    <Tile
      label="Данные по состоянию на"
      value={formatDate(status.data_edge)}
      sub="конец выгрузки заказчика, по всему парку сразу"
      note={
        status.computed_at
          ? `расчёт от ${formatDateTime(status.computed_at, true)}` +
            (разрыв.разошлись ? '' : `, ${разрыв.текст}`)
          : 'расчёта ещё не было'
      }
      warn={
        разрыв.разошлись && status.as_of
          ? `срез расчёта — ${formatDate(status.as_of)}: ${разрыв.текст}`
          : undefined
      }
    />
  )
}

function Tile({
  label,
  value,
  sub,
  note,
  warn,
  accent,
  href,
}: {
  label: string
  value: string
  sub?: string
  note?: string
  warn?: string
  accent?: string // цвет полоски слева — у плиток, где число требует действия
  href?: string // плитка ведёт туда, где с этим числом работают
}) {
  return (
    <article
      class="p-3 rounded flex flex-col gap-1 relative"
      style={`background:var(--bg-surface); border:1px solid var(--border-subtle)${accent ? `; border-left:4px solid ${accent}` : ''}`}
    >
      <h3 class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {label}
      </h3>
      <div class="num text-2xl font-semibold" style="font-family:var(--font-display)">
        {value}
      </div>
      {sub && (
        <div class="text-xs" style="color:var(--text-secondary)">
          {sub}
        </div>
      )}
      {note && (
        <div class="text-xs" style="color:var(--text-muted)">
          {note}
        </div>
      )}
      {warn && (
        <div class="text-xs" style="color:var(--state-warning)">
          {warn}
        </div>
      )}
      {href && <a href={href} class="absolute inset-0" aria-label={`${label}: ${value}`} />}
    </article>
  )
}
