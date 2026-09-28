import { useLayoutEffect, useRef, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { имяУчастка } from '../../lib/format'
import { isDense, riskColors, riskLabel, riskShape, type RiskClass } from './risk'
import type { Section } from './types'
import { fullView, isFullView, panView, zoomView, type ViewRange } from './viewport'

/* Одна линия оси — один префикс тега (MOS-181, приёмка e8). Коллектор дерева
   собирает несколько префиксов (у «объекта Зита» их пять: 798, 885, 888, 889,
   890), и пикет 0 у каждого свой — на одной оси участки разных префиксов легли
   бы друг на друга, метка под меткой не видна и не кликается, зум их не
   разводит (найдено e8 275 позиций, 575 участков из 3 173). Поэтому у каждого
   префикса своя ось и свой масштаб — тот же viewport.ts, что раньше держал
   один масштаб на коллектор, теперь держит его на линию. */

// Масштаб: одна единица чертежа — 1,3 px экрана, всегда. Ширина чертежа не
// постоянная, а ширина оси на экране, делённая на этот масштаб (ResizeObserver).
// До MOS-101 viewBox был 1000 единиц на любую ширину, и значок сжимался вместе
// с осью: дерево объектов слева отняло у оси ~280 px, значок «6 ед.» стал 81 px
// площади вместо 121, и US-05 сц. 5 (различимость без цвета, порог 10 %) упал
// с 17 % до 6 %. До дерева на окне 1280 масштаб был 1,24 (ось 1 240 px на 1000
// единиц); 1,3 — с запасом над порогом сц. 5 (решение c0 на ревью).
const PX_PER_UNIT = 1.3
const PADDING = 40
const MIN_VIEW_FRACTION = 0.01
// Высота чертежа и уровень оси. В обычной плотности над осью стоит рамка
// с номером пикета на выноске, поэтому ось ниже; в густой выносок нет,
// и пустая полоса над осью не нужна.
const LAYOUT = {
  dense: { height: 60, baseline: 24, mark: 6, band: 12 },
  normal: { height: 84, baseline: 46, mark: 10, band: 16 },
}
// Деления шкалы — не чаще, чем через 70 единиц: подпись «ПК 1200» занимает ~30.
const TICK_MIN_GAP = 70

// Шаг делений 1, 2 или 5 × 10ⁿ пикетов — самый мелкий, при котором подписи не
// сталкиваются. На всей линии 797 (231 пикет на ~1 000 ед.) это 20, после пяти
// шагов зума — 1.
export function tickStep(picketsPerUnit: number): number {
  const raw = Math.max(1, picketsPerUnit * TICK_MIN_GAP)
  const pow = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 5, 10].map((k) => k * pow).find((st) => st >= raw)!
}

// Цвет полосы состояния — обводка значка того же класса.
const bandColor = (cls: RiskClass) =>
  cls === 'high' || cls === 'normal' ? riskColors(cls).border : 'var(--border-strong)'

// Значок состояния — одна функция на все размеры: густой режим (6), значок
// на оси обычной плотности (10) и легенда (index.tsx, 10). Форма — riskShape, цвет — riskColors.
export function RiskMark({
  cls,
  cx,
  cy,
  size,
}: {
  cls: RiskClass
  cx: number
  cy: number
  size: number
}) {
  const c = riskColors(cls)
  const h = size / 2
  const shape = riskShape(cls)
  if (shape === 'triangle')
    return (
      <polygon
        points={`${cx},${cy - h} ${cx + h},${cy + h} ${cx - h},${cy + h}`}
        fill={c.fill}
        stroke={c.border}
        stroke-width={1}
      />
    )
  if (shape === 'circle')
    return <circle cx={cx} cy={cy} r={h} fill={c.fill} stroke={c.border} stroke-width={1} />
  // Вертикальная, а не горизонтальная: вдоль оси черта ложилась на саму линию
  // (MOS-50, находка 4d) и отличалась от куска оси только цветом.
  return <rect x={cx - size / 6} y={cy - h} width={size / 3} height={size} fill={c.text} />
}

interface AxisLineProps {
  prefix: string
  all: Section[]
  visible: Section[]
  riskBySection: Map<number, RiskClass>
  // Участок из /map?section= (MOS-245) — обводим кольцом, в любом режиме плотности.
  selected?: number
  viewRange: ViewRange | null
  onViewRangeChange: (v: ViewRange | null) => void
}

export function AxisLine({
  prefix,
  all,
  visible,
  riskBySection,
  selected,
  viewRange,
  onViewRangeChange,
}: AxisLineProps) {
  // Ширина оси на экране, px. null — ещё не измерили: тогда рисуем пустую рамку,
  // а не метки с x() от нулевой ширины (NaN и мигание первого кадра).
  const svgRef = useRef<SVGSVGElement>(null)
  const [screenWidth, setScreenWidth] = useState<number | null>(null)
  useLayoutEffect(() => {
    const el = svgRef.current
    if (!el) return
    setScreenWidth(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setScreenWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const AXIS_WIDTH = (screenWidth ?? 0) / PX_PER_UNIT

  const maxPicket = Math.max(1, ...all.map((s) => s.picket))
  const minViewWidth = Math.max(1, maxPicket * MIN_VIEW_FRACTION)
  const [viewStart, viewEnd] = viewRange ?? fullView(maxPicket)
  const viewWidth = viewEnd - viewStart
  const zoomed = !isFullView([viewStart, viewEnd], maxPicket)
  // Фильтры сужают набор значков, но не масштаб линии — тот держится на all,
  // не на visible, иначе включённый фильтр менял бы диапазон под ногами.
  const visibleAxis = visible.filter((s) => s.picket >= viewStart && s.picket <= viewEnd)

  const innerWidth = AXIS_WIDTH - PADDING * 2
  // Одна функция «пикет → x» на всё: ось, полосы, деления и значки.
  const x = (picket: number) => PADDING + ((picket - viewStart) / viewWidth) * innerWidth
  const dense = isDense(visibleAxis.map((s) => x(s.picket)))
  const L = dense ? LAYOUT.dense : LAYOUT.normal
  const baselineY = L.baseline
  const classOf = (s: Section) => riskBySection.get(s.section_id) ?? null

  // Полосы состояния: соседние участки одного класса сливаются в одну полосу,
  // если стоят на соседних пикетах или их значки на экране касаются. Так «бусы»
  // из 4–5 значков на ПК10–ПК14 читаются одним куском линии, а не россыпью.
  const bands: { cls: RiskClass; from: number; to: number; picket: number }[] = []
  for (const s of [...visibleAxis].sort((a, b) => a.picket - b.picket)) {
    const cls = classOf(s)
    const cx = x(s.picket)
    const last = bands.at(-1)
    if (last && last.cls === cls && (s.picket - last.picket <= 1 || cx - last.to <= L.band)) {
      last.to = cx
      last.picket = s.picket
    } else bands.push({ cls, from: cx, to: cx, picket: s.picket })
  }

  const step = tickStep(viewWidth / Math.max(1, innerWidth))
  const ticks: number[] = []
  if (innerWidth > 0)
    for (let pk = Math.ceil(viewStart / step) * step; pk <= viewEnd; pk += step) ticks.push(pk)

  const zoomBy = (factor: number, center = (viewStart + viewEnd) / 2) =>
    onViewRangeChange(zoomView([viewStart, viewEnd], factor, center, maxPicket, minViewWidth))
  const panBy = (fraction: number) =>
    onViewRangeChange(panView([viewStart, viewEnd], fraction, maxPicket))

  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const svgX = (e.clientX - rect.left) / PX_PER_UNIT
    const center = viewStart + ((svgX - PADDING) / innerWidth) * viewWidth
    zoomBy(e.deltaY > 0 ? 1.4 : 1 / 1.4, center)
  }

  const кнопки: [string, boolean, () => void][] = [
    ['+ приблизить', viewWidth <= minViewWidth, () => zoomBy(0.5)],
    ['− отдалить', !zoomed, () => zoomBy(2)],
    ['◀ левее', !zoomed || viewStart <= 0, () => panBy(-0.3)],
    ['правее ▶', !zoomed || viewEnd >= maxPicket, () => panBy(0.3)],
    ['вся линия', !zoomed, () => onViewRangeChange(null)],
  ]

  return (
    <div class="flex flex-col gap-1.5">
      <div class="flex items-center justify-between gap-x-4 gap-y-2 text-sm flex-wrap">
        <span style="color:var(--text-secondary)">
          <strong style="color:var(--text-primary)">Линия {prefix}</strong>, ПК
          {Math.round(viewStart)}–ПК
          {Math.round(viewEnd)} из ПК0–ПК{maxPicket} · {all.length} участков
        </span>
        <div
          role="group"
          aria-label={`Масштаб линии ${prefix}`}
          class="inline-flex flex-wrap rounded-md overflow-hidden"
          style="border:1px solid var(--border-strong)"
        >
          {кнопки.map(([имя, выключена, действие], i) => (
            <button
              key={имя}
              type="button"
              class="px-3 py-1 text-sm bg-[var(--bg-surface)] enabled:hover:bg-[var(--bg-row-hover)] disabled:opacity-40 disabled:cursor-not-allowed"
              style={`color:var(--text-primary)${i > 0 ? '; border-left:1px solid var(--border-subtle)' : ''}`}
              disabled={выключена}
              onClick={действие}
            >
              {имя}
            </button>
          ))}
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${AXIS_WIDTH} ${L.height}`}
        role="img"
        aria-label={`Линия ${prefix}, показан участок ПК${Math.round(viewStart)}–ПК${Math.round(viewEnd)} из ${all.length} участков`}
        class="w-full"
        height={L.height * PX_PER_UNIT}
        style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:8px"
        onWheel={onWheel}
      >
        {screenWidth != null && screenWidth > 0 && (
          <>
            <line
              x1={PADDING}
              y1={baselineY}
              x2={AXIS_WIDTH - PADDING}
              y2={baselineY}
              stroke="var(--border-subtle)"
              stroke-width="2"
              stroke-linecap="round"
            />
            {/* Полосы — полупрозрачный штрих: на ч/б распечатке (US-05 сц. 5) чёрный
            при 0,35 выходит светлее порога 128 и не закрывает силуэт значка поверх. */}
            {bands.map((b, i) => (
              <line
                key={i}
                x1={b.from}
                y1={baselineY}
                x2={b.to}
                y2={baselineY}
                stroke={bandColor(b.cls)}
                stroke-opacity={0.35}
                stroke-width={L.band}
                stroke-linecap="round"
                pointer-events="none"
              />
            ))}
            {ticks.map((pk) => (
              <g key={pk} pointer-events="none">
                <line
                  x1={x(pk)}
                  y1={baselineY + L.band / 2 + 3}
                  x2={x(pk)}
                  y2={baselineY + L.band / 2 + 7}
                  stroke="var(--border-strong)"
                  stroke-width={1}
                />
                <text
                  x={x(pk)}
                  y={baselineY + L.band / 2 + 19}
                  font-size="11"
                  text-anchor="middle"
                  fill="var(--text-muted)"
                >
                  ПК {pk}
                </text>
              </g>
            ))}
            {visibleAxis.map((s) => {
              const cls = classOf(s)
              const title = `${имяУчастка(s.smvu_key)} · участок ${s.section_id} · ${riskLabel(cls)}`
              const cx = x(s.picket)
              const выбран = s.section_id === selected
              const кольцо = выбран && (
                <circle
                  cx={cx}
                  cy={baselineY}
                  r={dense ? 7 : 9}
                  fill="none"
                  stroke="var(--brand-nav-marker)"
                  stroke-width={2.5}
                />
              )
              const метка = {
                'data-section-id': s.section_id,
                'data-selected': выбран ? '' : undefined,
              }

              // Густо — только значок на оси, без номера (правило плотности,
              // risk.ts): рамка 20×20 перекрыла бы соседей на этой оси.
              if (dense) {
                return (
                  <g
                    key={s.section_id}
                    {...метка}
                    style="cursor:pointer"
                    onClick={() => route(`/objects/${s.section_id}`)}
                  >
                    <title>{title}</title>
                    {кольцо}
                    {/* Цели клика 6×6 поверх формы нет нарочно: метки густой линии стоят
                    через 3,8 px, и невидимый квадрат соседа перехватывал клик в центр
                    видимого значка у 302 меток из 303 (линия 847). Кликается сама форма. */}
                    <RiskMark cls={cls} cx={cx} cy={baselineY} size={L.mark} />
                  </g>
                )
              }

              // Личность (рамка с номером на выноске) и состояние (значок на оси) —
              // раздельно, как на экране заказчика: номер читается при любом цвете.
              // Значок — последним: e2e US-05 сц. 5 снимает его как lastElementChild.
              return (
                <g
                  key={s.section_id}
                  {...метка}
                  style="cursor:pointer"
                  onClick={() => route(`/objects/${s.section_id}`)}
                >
                  <title>{title}</title>
                  {кольцо}
                  <line
                    x1={cx}
                    y1={24}
                    x2={cx}
                    y2={baselineY - L.band / 2}
                    stroke="var(--border-strong)"
                    stroke-width={1}
                  />
                  <rect
                    x={cx - 10}
                    y={4}
                    width={20}
                    height={20}
                    rx={4}
                    fill="var(--bg-table)"
                    stroke="var(--border-strong)"
                    stroke-width={1}
                  />
                  <text
                    x={cx}
                    y={18}
                    font-size="10"
                    font-weight="600"
                    text-anchor="middle"
                    fill="var(--text-primary)"
                  >
                    {Math.round(s.picket)}
                  </text>
                  <RiskMark cls={cls} cx={cx} cy={baselineY} size={L.mark} />
                </g>
              )
            })}
          </>
        )}
      </svg>
    </div>
  )
}
