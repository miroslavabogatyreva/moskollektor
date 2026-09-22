import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { errorMessage } from '../../lib/format'
import { DEFAULT_FILTERS, matchesFilters, type MapFilterState } from './filters'
import { MapFilters } from './MapFilters'
import { isDense, riskColors, riskLabel, type RiskClass } from './risk'
import type { RiskClassRow, Section } from './types'
import { fullView, isFullView, panView, zoomView, type ViewRange } from './viewport'

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

   Масштаб (5.15, MOS-126): на коллекторе с тегом 15 (до MOS-181 — номер в
   выпадающем списке, сейчас часть группы «объект Бета») из 74 пар соседних
   меток 49 стоят ближе 10 единиц SVG при диаметре метки 10 — на полной оси
   их не разлепить мышью. Приближение не перекладывает точки, а сужает видимый
   диапазон пикетов на той же ширине SVG — слипшиеся метки раздвигаются сами,
   без алгоритма разбежки. Арифметика окна — в viewport.ts, с самопроверкой. */

const AXIS_WIDTH = 1000
const AXIS_HEIGHT = 120
const PADDING = 40
const MIN_VIEW_FRACTION = 0.01

// ponytail: логин без входа, как в screens/dashboard/api.ts — заглушка до экрана
// логина (Q4.2, LDAP), заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

// Порядок легенды (MOS-170) — те же три состояния, что красит риск.ts. Слова
// должны дословно совпасть с легендой на дашборде (зона fe) — текст согласован
// в переписке к MOS-170, менять только вместе с ним.
const LEGEND_STATES: RiskClass[] = ['high', 'normal', null]

export function MapScreen(_props: Record<string, unknown>) {
  const [sections, setSections] = useState<Section[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collector, setCollector] = useState<number | null>(null)
  const [viewRange, setViewRange] = useState<ViewRange | null>(null)
  const [risks, setRisks] = useState<RiskClassRow[]>([])
  const [filters, setFilters] = useState<MapFilterState>(DEFAULT_FILTERS)

  useEffect(() => {
    fetch('/data/sections.json')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Section[]>
      })
      .then((data) => {
        setSections(data)
        setCollector(data[0]?.collector ?? null)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  useEffect(() => {
    fetch('/api/risks', { headers: { 'X-User-Login': API_LOGIN } })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<RiskClassRow[]>
      })
      .then(setRisks)
      // Риск не грузится — не блокируем схему, участки просто выйдут нейтральными.
      .catch((e) => console.error('не удалось загрузить /api/risks:', errorMessage(e)))
  }, [])

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

  const collectorName = collectors.find(([c]) => c === collector)?.[1].name ?? String(collector ?? '')

  const onAxis = useMemo(
    () => (sections && collector != null ? sections.filter((s) => s.collector === collector) : []),
    [sections, collector],
  )

  // Фильтры сужают набор значков, но не масштаб оси — линейка и подписи ПК держатся
  // на полном onAxis, иначе включённый фильтр менял бы диапазон под ногами.
  const filteredAxis = useMemo(
    () => onAxis.filter((s) => matchesFilters(s, riskBySection.get(s.section_id), filters)),
    [onAxis, riskBySection, filters],
  )

  // Смена коллектора меняет диапазон пикетов — старое окно просмотра теряет смысл.
  useEffect(() => setViewRange(null), [collector])

  const maxPicket = Math.max(1, ...onAxis.map((s) => s.picket))
  const minViewWidth = Math.max(1, maxPicket * MIN_VIEW_FRACTION)
  const [viewStart, viewEnd] = viewRange ?? fullView(maxPicket)
  const viewWidth = viewEnd - viewStart
  const zoomed = !isFullView([viewStart, viewEnd], maxPicket)
  const visibleAxis = useMemo(
    () => filteredAxis.filter((s) => s.picket >= viewStart && s.picket <= viewEnd),
    [filteredAxis, viewStart, viewEnd],
  )

  const innerWidth = AXIS_WIDTH - PADDING * 2
  const x = (picket: number) => PADDING + ((picket - viewStart) / viewWidth) * innerWidth
  const baselineY = AXIS_HEIGHT / 2
  const dense = isDense(visibleAxis.length, innerWidth)

  const zoomBy = (factor: number, center = (viewStart + viewEnd) / 2) =>
    setViewRange(zoomView([viewStart, viewEnd], factor, center, maxPicket, minViewWidth))
  const panBy = (fraction: number) =>
    setViewRange(panView([viewStart, viewEnd], fraction, maxPicket))
  const resetView = () => setViewRange(null)

  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const svgX = ((e.clientX - rect.left) / rect.width) * AXIS_WIDTH
    const center = viewStart + ((svgX - PADDING) / innerWidth) * viewWidth
    zoomBy(e.deltaY > 0 ? 1.4 : 1 / 1.4, center)
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Схема коллектора по пикетам
      </h1>

      {error && <p style="color:var(--state-error)">Не удалось загрузить участки: {error}</p>}
      {!sections && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {sections && (
        <>
          <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
            Коллектор
            <select
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              value={collector ?? undefined}
              onChange={(e) => setCollector(Number((e.target as HTMLSelectElement).value))}
            >
              {collectors.map(([c, g]) => (
                <option key={c} value={c}>
                  {g.name} · {g.count} участков
                </option>
              ))}
            </select>
          </label>

          <MapFilters
            filters={filters}
            onChange={setFilters}
            matchCount={filteredAxis.length}
            totalCount={onAxis.length}
          />

          <div class="flex items-center gap-2">
            <button
              type="button"
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              disabled={viewWidth <= minViewWidth}
              onClick={() => zoomBy(0.5)}
            >
              + приблизить
            </button>
            <button
              type="button"
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              disabled={!zoomed}
              onClick={() => zoomBy(2)}
            >
              − отдалить
            </button>
            <button
              type="button"
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              disabled={!zoomed || viewStart <= 0}
              onClick={() => panBy(-0.3)}
            >
              ◀ левее
            </button>
            <button
              type="button"
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              disabled={!zoomed || viewEnd >= maxPicket}
              onClick={() => panBy(0.3)}
            >
              правее ▶
            </button>
            <button
              type="button"
              class="text-sm px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
              disabled={!zoomed}
              onClick={resetView}
            >
              вся ось
            </button>
          </div>

          <svg
            viewBox={`0 0 ${AXIS_WIDTH} ${AXIS_HEIGHT}`}
            role="img"
            aria-label={`Ось пикетов коллектора ${collectorName}, показан участок ПК${Math.round(viewStart)}–ПК${Math.round(viewEnd)} из ${onAxis.length} участков`}
            class="w-full"
            style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px"
            onWheel={onWheel}
          >
            <line
              x1={PADDING}
              y1={baselineY}
              x2={AXIS_WIDTH - PADDING}
              y2={baselineY}
              stroke="var(--border-strong)"
              stroke-width="2"
            />
            <text x={PADDING} y={baselineY + 28} font-size="11" fill="var(--text-muted)">
              ПК{Math.round(viewStart)}
            </text>
            <text
              x={AXIS_WIDTH - PADDING}
              y={baselineY + 28}
              font-size="11"
              fill="var(--text-muted)"
              text-anchor="end"
            >
              ПК{Math.round(viewEnd)}
            </text>
            {visibleAxis.map((s) => {
              const cls = riskBySection.get(s.section_id)
              const colors = riskColors(cls)
              const title = `${s.smvu_key} · участок ${s.section_id} · ${riskLabel(cls)}`
              const cx = x(s.picket)

              // Густо — показываем только цветной чип без номера (правило плотности,
              // risk.ts): значок 20×20 перекрыл бы соседей на этой оси.
              if (dense) {
                return (
                  <rect
                    key={s.section_id}
                    x={cx - 3}
                    y={baselineY - 3}
                    width={6}
                    height={6}
                    fill={colors.fill}
                    stroke={colors.border}
                    stroke-width={1}
                    style="cursor:pointer"
                    onClick={() => route(`/objects/${s.section_id}`)}
                  >
                    <title>{title}</title>
                  </rect>
                )
              }

              // Личность (рамка с номером) и состояние (чип сбоку) — раздельно,
              // как на экране заказчика: номер читается при любом цвете чипа.
              return (
                <g
                  key={s.section_id}
                  style="cursor:pointer"
                  onClick={() => route(`/objects/${s.section_id}`)}
                >
                  <title>{title}</title>
                  <rect
                    x={cx - 10}
                    y={baselineY - 10}
                    width={20}
                    height={20}
                    rx={2}
                    fill="var(--bg-table)"
                    stroke="var(--border-strong)"
                    stroke-width={1.5}
                  />
                  <text
                    x={cx}
                    y={baselineY + 4}
                    font-size="9"
                    text-anchor="middle"
                    fill="var(--text-primary)"
                  >
                    {Math.round(s.picket)}
                  </text>
                  <rect
                    x={cx + 5}
                    y={baselineY - 13}
                    width={7}
                    height={7}
                    fill={colors.fill}
                    stroke={colors.border}
                    stroke-width={1}
                  />
                </g>
              )
            })}
          </svg>

          {/* Легенда состояний (MOS-170): названия рядом с цветом, не только
              в title значка — на настенном экране диспетчерской мышью не водят. */}
          <div
            class="flex flex-wrap items-center gap-4 text-sm"
            style="color:var(--text-secondary)"
          >
            {LEGEND_STATES.map((cls) => {
              const colors = riskColors(cls)
              return (
                <span key={String(cls)} class="flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    style={`display:inline-block;width:10px;height:10px;border-radius:2px;background:${colors.fill};border:1px solid ${colors.border}`}
                  />
                  {riskLabel(cls)}
                  {cls == null && (
                    <span style="color:var(--text-muted)"> — расчёта по объекту не было</span>
                  )}
                </span>
              )
            })}
          </div>
        </>
      )}
    </main>
  )
}
