import { route } from 'preact-router'
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

const AXIS_WIDTH = 1000
const AXIS_HEIGHT = 100
const PADDING = 40
const MIN_VIEW_FRACTION = 0.01

// Значок состояния — одна функция на все размеры: густой режим (6), чип рядом
// с номером (7) и легенда (index.tsx, 10). Форма — riskShape, цвет — riskColors.
export function RiskMark({ cls, cx, cy, size }: { cls: RiskClass; cx: number; cy: number; size: number }) {
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
  if (shape === 'circle') return <circle cx={cx} cy={cy} r={h} fill={c.fill} stroke={c.border} stroke-width={1} />
  // Вертикальная, а не горизонтальная: вдоль оси черта ложилась на саму линию
  // (MOS-50, находка 4d) и отличалась от куска оси только цветом.
  return <rect x={cx - size / 6} y={cy - h} width={size / 3} height={size} fill={c.text} />
}

interface AxisLineProps {
  prefix: string
  all: Section[]
  visible: Section[]
  riskBySection: Map<number, RiskClass>
  viewRange: ViewRange | null
  onViewRangeChange: (v: ViewRange | null) => void
}

export function AxisLine({ prefix, all, visible, riskBySection, viewRange, onViewRangeChange }: AxisLineProps) {
  const maxPicket = Math.max(1, ...all.map((s) => s.picket))
  const minViewWidth = Math.max(1, maxPicket * MIN_VIEW_FRACTION)
  const [viewStart, viewEnd] = viewRange ?? fullView(maxPicket)
  const viewWidth = viewEnd - viewStart
  const zoomed = !isFullView([viewStart, viewEnd], maxPicket)
  // Фильтры сужают набор значков, но не масштаб линии — тот держится на all,
  // не на visible, иначе включённый фильтр менял бы диапазон под ногами.
  const visibleAxis = visible.filter((s) => s.picket >= viewStart && s.picket <= viewEnd)

  const innerWidth = AXIS_WIDTH - PADDING * 2
  const x = (picket: number) => PADDING + ((picket - viewStart) / viewWidth) * innerWidth
  const baselineY = AXIS_HEIGHT / 2
  const dense = isDense(visibleAxis.map((s) => x(s.picket)))

  const zoomBy = (factor: number, center = (viewStart + viewEnd) / 2) =>
    onViewRangeChange(zoomView([viewStart, viewEnd], factor, center, maxPicket, minViewWidth))
  const panBy = (fraction: number) =>
    onViewRangeChange(panView([viewStart, viewEnd], fraction, maxPicket))
  const resetView = () => onViewRangeChange(null)

  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const svgX = ((e.clientX - rect.left) / rect.width) * AXIS_WIDTH
    const center = viewStart + ((svgX - PADDING) / innerWidth) * viewWidth
    zoomBy(e.deltaY > 0 ? 1.4 : 1 / 1.4, center)
  }

  return (
    <div class="flex flex-col gap-1">
      <div class="flex items-center gap-2 text-sm flex-wrap" style="color:var(--text-secondary)">
        <span>
          Линия {prefix}, ПК{Math.round(viewStart)}–ПК{Math.round(viewEnd)} из ПК0–ПК{maxPicket}
          {' '}· {all.length} участков
        </span>
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
          вся линия
        </button>
      </div>

      <svg
        viewBox={`0 0 ${AXIS_WIDTH} ${AXIS_HEIGHT}`}
        role="img"
        aria-label={`Линия ${prefix}, показан участок ПК${Math.round(viewStart)}–ПК${Math.round(viewEnd)} из ${all.length} участков`}
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
          const title = `${s.smvu_key} · участок ${s.section_id} · ${riskLabel(cls)}`
          const cx = x(s.picket)

          // Густо — показываем только цветной чип без номера (правило плотности,
          // risk.ts): значок 20×20 перекрыл бы соседей на этой оси.
          if (dense) {
            return (
              <g key={s.section_id} style="cursor:pointer" onClick={() => route(`/objects/${s.section_id}`)}>
                <title>{title}</title>
                {/* Цели клика 6×6 поверх формы нет нарочно: метки густой линии стоят
                    через 3,8 px, и невидимый квадрат соседа перехватывал клик в центр
                    видимого значка у 302 меток из 303 (линия 847). Кликается сама форма. */}
                <RiskMark cls={cls} cx={cx} cy={baselineY} size={6} />
              </g>
            )
          }

          // Личность (рамка с номером) и состояние (чип сбоку) — раздельно,
          // как на экране заказчика: номер читается при любом цвете чипа.
          return (
            <g key={s.section_id} style="cursor:pointer" onClick={() => route(`/objects/${s.section_id}`)}>
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
              <text x={cx} y={baselineY + 4} font-size="9" text-anchor="middle" fill="var(--text-primary)">
                {Math.round(s.picket)}
              </text>
              {/* Чип над правым углом рамки, не на кромке: на кромке черта сливалась с ней. */}
              <RiskMark cls={cls} cx={cx + 10} cy={baselineY - 16} size={7} />
            </g>
          )
        })}
      </svg>
    </div>
  )
}
