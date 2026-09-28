import { useEffect, useMemo, useState } from 'preact/hooks'
import { RiskBadge } from '../../components/RiskBadge'
import { Tile, UnackedPanel } from '../../components/Tiles'
import { apiFetch } from '../../lib/api'
import { usePoll, свежо } from '../../lib/poll'
import { errorMessage, formatDate, formatDateTime, имяУчастка } from '../../lib/format'
import { участков } from '../../lib/plural'
import { AxisLine, RiskMark } from './AxisLine'
import { DEFAULT_FILTERS, matchesFilters, type MapFilterState } from './filters'
import { MapFilters } from './MapFilters'
import { ObjectTree, type TreeCollector } from './ObjectTree'
import { riskLabel, type RiskClass } from './risk'
import { DEMO_NODE, SensorDemo } from './SensorDemo'
import {
  sensorRiskUrl,
  синтетикаВключена,
  срезРасчёта,
  type SensorRiskPage,
  type SensorSummary,
} from '../../lib/sensorRisk'
import { fetchOrdersSummary, fetchUnacked } from '../dashboard/api'
import { useSensorSummary } from '../dashboard/SensorsView'
import type { OrdersSummary, Unacked } from '../dashboard/types'
import type { RiskClassRow, Section } from './types'
import { fullView, zoomView, type ViewRange } from './viewport'

/* Ось пикетов — первая половина задачи 5.3 (MOS-50): сам чертёж и клик по метке
   ведёт на /objects/:sectionId (ObjectCard, 5.5, MOS-52). Цвет значка по уровню
   риска — задача 5.22 (MOS-106): личность объекта (рамка с номером) и его
   состояние (маленький чип сбоку) нарисованы раздельно, как на экране заказчика
   (docs/meetings/img-forum/19-10-интерфейс-смву-крупно.png) — номер читается
   при любом цвете чипа. Цвета и правило плотности — risk.ts, с самопроверкой.
   3 173 участка одной лентой не показать (dashboard/dashboard.md, разд. 9) —
   поэтому сначала выбор коллектора, потом ось. Заголовок экрана отличается
   от подписи в меню: меню держится за формулировку М-05 ("Карта объектов"),
   а здесь — про способ показа.

   Коллектор дерева заказчика (MOS-181) собирает несколько префиксов тега
   («объект Зита» — пять), и пикет 0 у каждого свой. Одна общая ось наложила бы
   их участки друг на друга (275 позиций, 575 участков из 3 173 — нашла e8) —
   поэтому под коллектором рисуется по одной линии AxisLine.tsx на префикс,
   каждая со своим масштабом (5.15, MOS-126, арифметика — в viewport.ts).

   С 28.09.2026 это главный экран (MOS-265, план 5.41), как рабочее место СМВУ 2.0:
   сверху полоса-сводка, справа «Ждут квитирования», посередине — схема коллектора
   с наибольшим числом датчиков высокого риска (top_collectors[0] из
   GET /api/sensor-risk/summary). Ось участков — под переключателем ?axis=sections:
   риск участка — это риск коллектора, разнесённый на все его участки, и на Гамме
   она красила высоким 400 участков из 400, а ось датчиков — 6 датчиков из 1 081. */

// Порядок легенды (MOS-170) — те же три состояния, что красит риск.ts. Слова
// должны дословно совпасть с легендой на дашборде (зона fe) — текст согласован
// в переписке к MOS-170, менять только вместе с ним.
const LEGEND_STATES: RiskClass[] = ['high', 'normal', null]
// «объект Каппа» — коллектор узла демо по датчикам (DEMO_NODE, «объект Каппа ДУ»).
const KAPPA = 15

// Сообщение, если датчика из адреса /map?channel=<id> нет в прогнозе или он вне зоны роли.
const НЕТ_ДАТЧИКА = 'датчика нет в прогнозе или он вне вашей зоны'

// section — из адреса /map?section=<id> (preact-router кладёт параметры запроса
// в props): переход «на схеме» из полосы уведомлений, Ф-90, MOS-245.
// demo=sensors — /map?demo=sensors: выбрать «объект Каппа ДУ» и прокрутить к демо
// прогноза по датчикам (SensorDemo.tsx).
// Прогноз по датчикам (эпик MOS-248, SL.6): channel=<id> — датчик из таблицы
// дашборда, схема выбирает его коллектор, а SensorDemo — линию, пикет и строку;
// collector=<id> — коллектор из «Где риск сосредоточен»; synthetic=0 — паспорт
// оборудования не учитывать (переключатель, SyntheticToggle.tsx).
export function MapScreen({
  section,
  demo,
  channel,
  collector: collectorParam,
  synthetic,
  axis,
}: {
  section?: string
  demo?: string
  channel?: string
  collector?: string
  synthetic?: string
  axis?: string
} & Record<string, unknown>) {
  const синтетика = синтетикаВключена(synthetic)
  // Участок из адреса (переход «на схеме» из полосы уведомлений) рисуется на оси участков.
  const поУчасткам = axis === 'sections' || section != null
  const датчик = channel ? Number(channel) : undefined
  const [всеУчастки, setВсеУчастки] = useState<Section[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collector, setCollector] = useState<number | null>(null)
  // Своё окно просмотра на каждую линию (префикс), не одно на коллектор.
  const [viewRanges, setViewRanges] = useState<Record<string, ViewRange | null>>({})
  const [risks, setRisks] = useState<RiskClassRow[]>([])
  const [filters, setFilters] = useState<MapFilterState>(DEFAULT_FILTERS)
  // Дерево «коллектор → узел» (5.9, MOS-101) и выбранный в нём узел. Узел сужает
  // метки оси так же, как фильтры, — поверх них, а не вместо.
  const [tree, setTree] = useState<TreeCollector[]>([])
  const [node, setNode] = useState<number | null>(null)
  const [treeError, setTreeError] = useState<string | null>(null)
  const [treeLoaded, setTreeLoaded] = useState(false)

  // Только свои участки (US-16 сц. 1, US-21): /data/sections.json — статический
  // справочник всего парка, а дерево GET /api/objects/tree сервер режет по области
  // видимости роли. Техник комплекса «Бета» раньше видел все 16 коллекторов
  // и открывал схему на чужом «Альфа» со 112 участками. Дерево не пришло — берём
  // весь справочник: риски сервер режет и так, а схема без коллекторов хуже.
  const sections = useMemo(() => {
    if (!всеУчастки) return null
    if (treeError) return всеУчастки
    if (!treeLoaded) return null
    const свои = new Set(tree.map((c) => c.object_id))
    return всеУчастки.filter((s) => свои.has(s.collector))
  }, [всеУчастки, tree, treeLoaded, treeError])
  // Сводка по датчикам, уведомления и заявки — раз в минуту от общего опроса (НФ-89).
  const { tick } = usePoll()
  const сводка = useSensorSummary(синтетика, tick, true)
  const [unacked, setUnacked] = useState<Unacked | null>(null)
  const [orders, setOrders] = useState<OrdersSummary | null>(null)
  useEffect(() => {
    let отменено = false
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

  // Демо по датчикам — только если коллектор Каппы роли виден (дерево сервер режет по роли).
  const демоДатчиков = demo === 'sensors'

  // Коллектор по умолчанию — с наибольшим числом датчиков высокого риска из своей зоны.
  // Без коллектора в адресе ждём сводку (или её отказ): иначе схема сперва открылась бы
  // на первом по списку и перескочила. Коллектор из адреса ставят эффекты ниже.
  const адресВыбрал = !!(collectorParam || section || channel || демоДатчиков)
  useEffect(() => {
    if (!sections || sections.some((s) => s.collector === collector)) return
    if (!адресВыбрал && !сводка.summary && !сводка.error) return
    const топ = сводка.summary?.top_collectors.find((c) =>
      sections.some((s) => s.collector === c.collector_id),
    )
    setCollector(топ?.collector_id ?? sections[0]?.collector ?? null)
  }, [sections, сводка.summary, сводка.error])
  useEffect(() => {
    if (!демоДатчиков || !sections?.some((s) => s.collector === KAPPA)) return
    setCollector(KAPPA)
    setNode(DEMO_NODE)
  }, [sections, демоДатчиков])

  // Коллектор из адреса — ссылка «Где риск сосредоточен» с дашборда по датчикам.
  useEffect(() => {
    const id = Number(collectorParam)
    if (!collectorParam || !sections?.some((s) => s.collector === id)) return
    setCollector(id)
    setNode(null)
  }, [sections, collectorParam])

  // Датчик из адреса: коллектор узнаём у сервера — в справочнике участков каналов нет.
  const [датчикОшибка, setДатчикОшибка] = useState<string | null>(null)
  useEffect(() => {
    setДатчикОшибка(null)
    if (датчик == null || !sections) return
    let отменено = false
    apiFetch(sensorRiskUrl({ synthetic: синтетика, channel: датчик, limit: 1 }))
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<SensorRiskPage>
      })
      .then((d) => {
        if (отменено) return
        const s = d.items.find((x) => x.channel_id === датчик)
        if (!s || !sections.some((x) => x.collector === s.collector_id))
          return setДатчикОшибка(НЕТ_ДАТЧИКА)
        setCollector(s.collector_id)
        setNode(null)
      })
      .catch((e) => !отменено && setДатчикОшибка(errorMessage(e)))
    return () => {
      отменено = true
    }
  }, [sections, датчик])

  useEffect(() => {
    fetch('/data/sections.json')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Section[]>
      })
      .then(setВсеУчастки)
      .catch((e) => setError(errorMessage(e)))
  }, [])

  // Риски на оси — раз в минуту от общего опроса (НФ-89, MOS-123), и только
  // для оси участков: 435 КБ раз в минуту режиму «по датчикам» не нужны.
  useEffect(() => {
    if (!поУчасткам) return
    // Перезапуск раз в минуту — ответ прошлого тика выключаем, как на дашборде.
    let отменено = false
    apiFetch('/api/risks')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<RiskClassRow[]>
      })
      .then((r) => {
        if (отменено) return
        setRisks(r)
        свежо()
      })
      // Риск не грузится — не блокируем схему, участки просто выйдут нейтральными.
      .catch((e) => console.error('не удалось загрузить /api/risks:', errorMessage(e)))
    return () => {
      отменено = true
    }
  }, [tick, поУчасткам])

  useEffect(() => {
    apiFetch('/api/objects/tree')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<TreeCollector[]>
      })
      .then((t) => {
        setTree(t)
        setTreeLoaded(true)
      })
      // Дерево не грузится — говорим об этом на его месте, схема со списком коллекторов работает дальше.
      .catch((e) => setTreeError(errorMessage(e)))
  }, [])

  // Участок из адреса: выбираем его коллектор, приближаем его линию к пикету
  // (окно в десятую часть линии) и обводим метку. Эффект по участку, а не по
  // монтированию: ссылка из полосы на уже открытой схеме меняет только параметр.
  const выбранный = useMemo(
    () =>
      sections && section ? sections.find((s) => s.section_id === Number(section)) : undefined,
    [sections, section],
  )
  useEffect(() => {
    if (!выбранный || !sections) return
    const prefix = выбранный.smvu_key.split(':')[0]
    const max = Math.max(
      1,
      ...sections.filter((s) => s.smvu_key.startsWith(`${prefix}:`)).map((s) => s.picket),
    )
    setCollector(выбранный.collector)
    setNode(null)
    // Фильтр мог спрятать ту самую метку, ради которой перешли, — сбрасываем.
    setFilters(DEFAULT_FILTERS)
    setViewRanges({ [prefix]: zoomView(fullView(max), 0.1, выбранный.picket, max, 1) })
  }, [выбранный])

  const selectCollector = (id: number) => {
    setCollector(id)
    setNode(null)
  }

  const riskBySection = useMemo(() => {
    const m = new Map<number, RiskClassRow['risk_class']>()
    for (const r of risks) m.set(r.section_id, r.risk_class)
    return m
  }, [risks])

  // Группа — collector_id дерева объектов заказчика (MOS-181, М-05), подпись —
  // collector_name оттуда же, не голый номер тега.
  const collectors = useMemo(() => {
    if (!sections) return []
    const byId = new Map<number, { name: string; count: number }>()
    for (const s of sections) {
      const g = byId.get(s.collector) ?? { name: s.collector_name ?? String(s.collector), count: 0 }
      g.count += 1
      byId.set(s.collector, g)
    }
    return [...byId.entries()].sort((a, b) => a[0] - b[0])
  }, [sections])

  const collectorName =
    collectors.find(([c]) => c === collector)?.[1].name ?? String(collector ?? '')

  const onAxis = useMemo(
    () => (sections && collector != null ? sections.filter((s) => s.collector === collector) : []),
    [sections, collector],
  )

  // Фильтры сужают набор значков, но не масштаб оси — линейка и подписи ПК держатся
  // на полном onAxis, иначе включённый фильтр менял бы диапазон под ногами.
  // Участки выбранного узла. Принадлежность — по каналам (GET /api/objects/tree),
  // а ось раскладывает участок по большинству каналов, поэтому узел может держать
  // участок с оси ДРУГОГО коллектора (так 1490 у «ДП объект Бета»): такие не рисуем
  // на чужой оси, а называем под схемой — offAxis ниже.
  const nodeSections = useMemo(() => {
    const n = tree.flatMap((c) => c.nodes).find((x) => x.object_id === node)
    return n ? new Set(n.section_ids) : null
  }, [tree, node])

  const filteredAxis = useMemo(
    () =>
      onAxis.filter(
        (s) =>
          (!nodeSections || nodeSections.has(s.section_id)) &&
          matchesFilters(s, riskBySection.get(s.section_id), filters),
      ),
    [onAxis, riskBySection, filters, nodeSections],
  )

  const offAxis = useMemo(() => {
    if (!nodeSections || !sections) return []
    const byId = new Map(sections.map((s) => [s.section_id, s]))
    return [...nodeSections]
      .map((id) => byId.get(id))
      .filter((s): s is Section => !!s && s.collector !== collector)
  }, [nodeSections, sections, collector])

  // Смена коллектора меняет набор линий — старые окна просмотра теряют смысл.
  // Кроме смены по участку из адреса: там окно только что выставлено под пикет.
  useEffect(() => {
    if (выбранный?.collector !== collector) setViewRanges({})
  }, [collector])

  // Линия — один префикс тега (MOS-181): smvu_key = "префикс:пикет", и у каждого
  // префикса пикет 0 свой. all — на масштаб линии (фильтры его не двигают),
  // filteredByPrefix — что внутри линии показывать.
  const lines = useMemo(() => {
    const byPrefix = new Map<string, Section[]>()
    for (const s of onAxis) {
      const prefix = s.smvu_key.split(':')[0]
      byPrefix.set(prefix, [...(byPrefix.get(prefix) ?? []), s])
    }
    return [...byPrefix.entries()].sort((a, b) => Number(a[0]) - Number(b[0]))
  }, [onAxis])

  const filteredByPrefix = useMemo(() => {
    const m = new Map<string, Section[]>()
    for (const s of filteredAxis) {
      const prefix = s.smvu_key.split(':')[0]
      m.set(prefix, [...(m.get(prefix) ?? []), s])
    }
    return m
  }, [filteredAxis])

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Схема коллектора по пикетам
      </h1>

      <HomeStrip
        summary={сводка.summary}
        summaryError={сводка.error}
        unacked={unacked}
        orders={orders}
      />

      {error && <p style="color:var(--state-error)">Не удалось загрузить участки: {error}</p>}
      {!sections && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {/* Три колонки с 1280 px: дерево, схема, журнал. Уже — одной колонкой в том же
          порядке, журнал под схемой (на 390 px без горизонтальной прокрутки). */}
      <div class="flex flex-col xl:flex-row gap-5 xl:items-start">
        {sections && (
          <div class="flex flex-col md:flex-row gap-5 md:items-start flex-1 min-w-0">
            {treeError ? (
              <p class="text-sm w-64 shrink-0" style="color:var(--state-error)">
                Дерево объектов не загрузилось: {treeError}
              </p>
            ) : (
              tree.length > 0 && (
                <ObjectTree
                  tree={tree}
                  collector={collector}
                  node={node}
                  onCollector={selectCollector}
                  onNode={setNode}
                />
              )
            )}
            <div class="flex flex-col gap-4 flex-1 min-w-0">
              <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
                Коллектор
                <select
                  class="text-sm px-2 py-1 rounded"
                  style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
                  value={collector ?? undefined}
                  onChange={(e) => selectCollector(Number((e.target as HTMLSelectElement).value))}
                >
                  {collectors.map(([c, g]) => (
                    <option key={c} value={c}>
                      {g.name} · {участков(g.count)}
                    </option>
                  ))}
                </select>
              </label>

              <AxisSwitch поУчасткам={поУчасткам} collector={collector} синтетика={синтетика} />

              {поУчасткам && (
                <>
                  <MapFilters
                    filters={filters}
                    onChange={setFilters}
                    matchCount={filteredAxis.length}
                    totalCount={onAxis.length}
                  />

                  {выбранный && (
                    <p class="text-sm" style="color:var(--text-primary)">
                      {/* Имя — по префиксу smvu_key, как у сервера («Коллектор 884, пикет 730»), а не по collector. */}
                      Выбран участок: {имяУчастка(выбранный.smvu_key)}
                    </p>
                  )}

                  <p class="text-sm" style="color:var(--text-secondary)">
                    Коллектор «{collectorName}»: {lines.length}{' '}
                    {lines.length === 1 ? 'линия' : 'линии'}
                  </p>

                  <div class="flex flex-col gap-4">
                    {lines.map(([prefix, all]) => (
                      <AxisLine
                        key={prefix}
                        prefix={prefix}
                        all={all}
                        visible={filteredByPrefix.get(prefix) ?? []}
                        riskBySection={riskBySection}
                        selected={выбранный?.section_id}
                        viewRange={viewRanges[prefix] ?? null}
                        onViewRangeChange={(v) =>
                          setViewRanges((prev) => ({ ...prev, [prefix]: v }))
                        }
                      />
                    ))}
                  </div>

                  {offAxis.length > 0 && (
                    <p data-testid="off-axis" class="text-sm" style="color:var(--text-secondary)">
                      На оси другого коллектора — у них там больше каналов:{' '}
                      {offAxis.map((s, i) => (
                        <span key={s.section_id}>
                          {i > 0 && ', '}участок {s.section_id} ({s.smvu_key}),{' '}
                          <button
                            type="button"
                            class="underline"
                            style="color:var(--link)"
                            onClick={() => selectCollector(s.collector)}
                          >
                            {s.collector_name ?? s.collector}
                          </button>
                        </span>
                      ))}
                    </p>
                  )}

                  {/* Легенда состояний (MOS-170): названия рядом с цветом, не только
              в title значка — на настенном экране диспетчерской мышью не водят.
              Значок оси и бейдж с тем же словом, что в столбце «Риск» дашборда. */}
                  <div
                    class="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm"
                    style="color:var(--text-secondary)"
                  >
                    {LEGEND_STATES.map((cls) => (
                      <span key={String(cls)} class="flex items-center gap-2">
                        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
                          <RiskMark cls={cls} cx={6} cy={6} size={10} />
                        </svg>
                        <RiskBadge cls={cls}>{riskLabel(cls)}</RiskBadge>
                        {cls == null && (
                          <span style="color:var(--text-muted)">— расчёта по объекту не было</span>
                        )}
                      </span>
                    ))}
                  </div>
                </>
              )}

              {датчикОшибка && (
                <p class="text-sm" style="color:var(--state-error)">
                  Датчик {датчик} не открыть: {датчикОшибка}
                </p>
              )}
              {collector != null && (
                <SensorDemo
                  collector={collector}
                  collectorName={collectorName}
                  sections={всеУчастки ?? []}
                  synthetic={синтетика}
                  channel={датчик}
                  scrollTo={демоДатчиков || датчик != null}
                />
              )}
            </div>
          </div>
        )}
        <aside aria-label="Журнал" class="xl:w-96 shrink-0">
          <UnackedPanel unacked={unacked} />
        </aside>
      </div>
    </main>
  )
}

// Переключатель оси — ссылками, режим живёт в адресе (?axis=sections), как у
// дашборда: коллектор и синтетика едут вместе с переходом.
function AxisSwitch({
  поУчасткам,
  collector,
  синтетика,
}: {
  поУчасткам: boolean
  collector: number | null
  синтетика: boolean
}) {
  const q = `${collector != null ? `collector=${collector}&` : ''}${синтетика ? '' : 'synthetic=0&'}`
  const пункты: [string, string, boolean][] = [
    ['по датчикам', `/map?${q}`.replace(/[?&]$/, ''), !поУчасткам],
    ['по участкам', `/map?${q}axis=sections`, поУчасткам],
  ]
  return (
    <nav
      aria-label="Ось схемы"
      class="inline-flex self-start rounded-md overflow-hidden text-sm"
      style="border:1px solid var(--border-strong)"
    >
      {пункты.map(([имя, href, выбран], i) => (
        <a
          key={имя}
          href={href}
          aria-current={выбран ? 'page' : undefined}
          class="px-3 py-1"
          style={`text-decoration:none; ${выбран ? 'background:var(--brand); color:var(--text-on-brand)' : 'background:var(--bg-surface); color:var(--text-primary)'}${i > 0 ? '; border-left:1px solid var(--border-strong)' : ''}`}
        >
          {имя}
        </a>
      ))}
    </nav>
  )
}

// Полоса-сводка над схемой: те же плитки, что на дашборде, в одну строку.
function HomeStrip({
  summary,
  summaryError,
  unacked,
  orders,
}: {
  summary: SensorSummary | null
  summaryError: string | null
  unacked: Unacked | null
  orders: OrdersSummary | null
}) {
  const нет = summaryError ? '—' : '…'
  return (
    <div
      data-testid="home-strip"
      class="grid gap-3"
      style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))"
    >
      <Tile
        label="▲ Высокий риск"
        value={summary ? String(summary.high) : нет}
        sub={summaryError ? 'сводка по датчикам не загрузилась' : 'датчиков'}
        accent={summary && summary.high > 0 ? 'var(--risk-critical-border)' : undefined}
        href="/dashboard?level=high"
      />
      <Tile
        label="◆ Наблюдать"
        value={summary ? String(summary.watch) : нет}
        sub={summary ? `в норме ${summary.normal}` : undefined}
        accent={summary && summary.watch > 0 ? 'var(--risk-medium-border)' : undefined}
        href="/dashboard?level=watch"
      />
      <Tile
        label="Неквитированные"
        value={unacked ? String(unacked.total) : '…'}
        sub={unacked ? (unacked.total > 0 ? 'уведомлений' : 'все квитированы') : undefined}
        accent={unacked && unacked.total > 0 ? 'var(--state-warning)' : undefined}
        href="/orders"
      />
      <Tile
        label="Заявки в работе"
        value={orders ? String(orders.open) : '…'}
        sub={orders ? (orders.overdue > 0 ? undefined : 'просроченных нет') : undefined}
        warn={orders && orders.overdue > 0 ? `из них просрочено ${orders.overdue}` : undefined}
        href="/orders"
      />
      <Tile
        label="Срез данных"
        value={summary?.as_of ? formatDate(summary.as_of) : summary ? '—' : нет}
        sub={summary ? срезРасчёта(summary.as_of, formatDateTime) : undefined}
      />
    </div>
  )
}
