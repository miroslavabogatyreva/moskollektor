import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { apiFetch } from '../../lib/api'
import { errorMessage, formatDate, formatDateTime } from '../../lib/format'
import { tickStep } from './AxisLine'
import { riskColors } from './risk'
import { isFullView, panView, zoomView, type ViewRange } from './viewport'

/* Демо «прогноз на уровне датчика» на узле «объект Каппа ДУ» (smvu.object_tree
   5657, 188 каналов, ПК508…ПК863). Балл и причины считает GET /api/sensor-risk;
   паспорта оборудования синтетические (docs/sensor-level-proposal.md), история
   отказов реальная — об этом плашка сверху. Полоса пикетов: каждый датчик — точка
   на своём пикете, датчики одного пикета стопкой (на ПК632 их 42). */

export const DEMO_NODE = 5657

type Level = 'high' | 'watch' | 'normal'
interface Reason {
  text: string
  weight: number
  kind: 'real' | 'synthetic' | 'plan'
}
interface Equipment {
  equipment_no: string
  manufacturer: string
  model_no: string
  in_service_from: string
  service_life_years: number
  last_check_at: string | null
  last_check_ok: boolean | null
}
interface Sensor {
  channel_id: number
  name: string
  sensor_kind: string
  picket: number
  section_id: number | null
  score: number
  level: Level
  reasons: Reason[]
  equipment: Equipment | null
}
interface SensorRisk {
  node: number
  node_name: string
  as_of: string
  synthetic: boolean
  items: Sensor[]
}

// Цвета — те же токены, что у оси (risk.ts): high и normal оттуда; для «наблюдать»
// берём --risk-medium, который tokens.css держит под трёхуровневую шкалу.
const LEVELS: Record<Level, { label: string; fill: string; text: string; border: string }> = {
  high: { label: 'высокий риск', ...riskColors('high') },
  watch: {
    label: 'наблюдать',
    fill: 'var(--risk-medium)',
    text: 'var(--risk-medium-text)',
    border: 'var(--risk-medium-border)',
  },
  normal: { label: 'норма', ...riskColors('normal') },
}
const ORDER: Level[] = ['high', 'watch', 'normal']

// Значок датчика: high — треугольник, watch — ромб, normal — круг (форма читается
// без цвета, как на оси). Датчик, снятый по графику ППР, обведён кольцом --state-info.
function Dot({
  s,
  cx,
  cy,
  r,
}: {
  s: Pick<Sensor, 'level' | 'reasons'>
  cx: number
  cy: number
  r: number
}) {
  const c = LEVELS[s.level]
  const p = { fill: c.fill, stroke: c.border, 'stroke-width': 1 }
  const shape =
    s.level === 'high' ? (
      <polygon
        points={`${cx},${cy - r - 1} ${cx + r + 1},${cy + r} ${cx - r - 1},${cy + r}`}
        {...p}
      />
    ) : s.level === 'watch' ? (
      <polygon
        points={`${cx},${cy - r - 1} ${cx + r + 1},${cy} ${cx},${cy + r + 1} ${cx - r - 1},${cy}`}
        {...p}
      />
    ) : (
      <circle cx={cx} cy={cy} r={r} {...p} />
    )
  return (
    <>
      {shape}
      {s.reasons.some((x) => x.kind === 'plan') && (
        <circle
          cx={cx}
          cy={cy}
          r={r + 2.5}
          fill="none"
          stroke="var(--state-info)"
          stroke-width={1.5}
        />
      )}
    </>
  )
}

function Badge({ level }: { level: Level }) {
  const c = LEVELS[level]
  return (
    <span
      data-level={level}
      class="inline-block rounded-full text-xs whitespace-nowrap"
      style={`padding:1px 8px; background:${c.fill}; color:${c.text}; border:1px solid ${c.border}`}
    >
      {c.label}
    </span>
  )
}

const STEP = 7 // шаг стопки по вертикали, px
const PAD = 24
const BASE_GAP = 34 // от оси до низа чертежа: деления и подписи ПК

export function SensorDemo({ scrollTo }: { scrollTo?: boolean }) {
  const [data, setData] = useState<SensorRisk | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picket, setPicket] = useState<number | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [view, setView] = useState<ViewRange | null>(null)
  const rootRef = useRef<HTMLElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    apiFetch(`/api/sensor-risk?node=${DEMO_NODE}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<SensorRisk>
      })
      .then((d) => {
        setData(d)
        // По умолчанию — пикет датчика с самым высоким баллом (items отсортированы сервером).
        setPicket(d.items[0]?.picket ?? null)
        setOpen(d.items[0]?.channel_id ?? null)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  useEffect(() => {
    if (scrollTo && data) rootRef.current?.scrollIntoView({ block: 'start' })
  }, [scrollTo, data])

  useLayoutEffect(() => {
    const el = svgRef.current
    if (!el) return
    setWidth(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [data])

  // Датчики по пикетам, внутри стопки снизу вверх по возрастанию балла — красные наверху.
  const stacks = useMemo(() => {
    const m = new Map<number, Sensor[]>()
    for (const s of data?.items ?? []) m.set(s.picket, [...(m.get(s.picket) ?? []), s])
    for (const v of m.values()) v.sort((a, b) => a.score - b.score)
    return [...m.entries()].sort((a, b) => a[0] - b[0])
  }, [data])

  if (error)
    return (
      <p class="text-sm" style="color:var(--state-error)">
        Прогноз по датчикам не загрузился: {error}
      </p>
    )
  if (!data) return <p style="color:var(--text-muted)">Загрузка прогноза по датчикам…</p>

  // viewport.ts считает окно от 0 до max — работаем в сдвиге от первого пикета.
  const lo = stacks[0]?.[0] ?? 0
  const max = Math.max(1, (stacks.at(-1)?.[0] ?? 1) - lo)
  const [v0, v1] = view ?? [0, max]
  const inner = Math.max(1, width - PAD * 2)
  const x = (pk: number) => PAD + ((pk - lo - v0) / (v1 - v0)) * inner
  const tallest = Math.max(...stacks.map(([, v]) => v.length))
  const baseY = 12 + tallest * STEP
  const height = baseY + BASE_GAP
  const step = tickStep((v1 - v0) / inner)
  const ticks: number[] = []
  for (let pk = Math.ceil((lo + v0) / step) * step; pk <= lo + v1; pk += step) ticks.push(pk)
  const zoomed = !isFullView([v0, v1], max)
  const zoom = (f: number, c = picket != null ? picket - lo : (v0 + v1) / 2) =>
    setView(zoomView([v0, v1], f, c, max, 5))
  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    zoom(e.deltaY > 0 ? 1.4 : 1 / 1.4, v0 + ((e.clientX - rect.left - PAD) / inner) * (v1 - v0))
  }
  const кнопки: [string, boolean, () => void][] = [
    ['+ приблизить', v1 - v0 <= 5, () => zoom(0.5)],
    ['− отдалить', !zoomed, () => zoom(2)],
    ['◀ левее', !zoomed || v0 <= 0, () => setView(panView([v0, v1], -0.3, max))],
    ['правее ▶', !zoomed || v1 >= max, () => setView(panView([v0, v1], 0.3, max))],
    ['весь участок', !zoomed, () => setView(null)],
  ]

  const selected = stacks.find(([pk]) => pk === picket)?.[1] ?? []
  const list = [...selected].sort((a, b) => b.score - a.score)
  const count = (l: Level) => data.items.filter((s) => s.level === l).length

  return (
    <section
      ref={rootRef}
      data-testid="sensor-demo"
      aria-label="Прогноз по датчикам"
      class="flex flex-col gap-3 rounded-lg p-4"
      style="background:var(--bg-table); border:1px solid var(--border-subtle); scroll-margin-top:16px"
    >
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h2 class="text-base font-semibold" style="font-family:var(--font-display)">
          Прогноз по датчикам · {data.node_name}
        </h2>
        <span class="text-sm" style="color:var(--text-secondary)">
          расчёт на {formatDateTime(data.as_of)} · {data.items.length} датчиков: {count('high')}{' '}
          высокий риск, {count('watch')} наблюдать, {count('normal')} норма
        </span>
      </div>

      {data.synthetic && (
        <p
          role="note"
          class="text-sm rounded px-3 py-2"
          style="background:var(--risk-medium); color:var(--risk-medium-text); border:1px solid var(--risk-medium-border)"
        >
          <strong>Демо:</strong> паспорта оборудования синтетические, история отказов реальная.
        </p>
      )}

      <div class="flex items-center justify-between gap-x-4 gap-y-2 text-sm flex-wrap">
        <span style="color:var(--text-secondary)">
          ПК{Math.round(lo + v0)}–ПК{Math.round(lo + v1)} · нажмите на стопку, чтобы открыть датчики
          пикета
        </span>
        <div
          role="group"
          aria-label="Масштаб полосы датчиков"
          class="inline-flex flex-wrap rounded-md overflow-hidden"
          style="border:1px solid var(--border-strong)"
        >
          {кнопки.map(([имя, выкл, действие], i) => (
            <button
              key={имя}
              type="button"
              class="px-3 py-1 text-sm bg-[var(--bg-surface)] enabled:hover:bg-[var(--bg-row-hover)] disabled:opacity-40 disabled:cursor-not-allowed"
              style={`color:var(--text-primary)${i > 0 ? '; border-left:1px solid var(--border-subtle)' : ''}`}
              disabled={выкл}
              onClick={действие}
            >
              {имя}
            </button>
          ))}
        </div>
      </div>

      <svg
        ref={svgRef}
        role="img"
        aria-label={`Датчики узла ${data.node_name} по пикетам`}
        class="w-full"
        height={height}
        viewBox={`0 0 ${width || 1} ${height}`}
        style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:8px"
        onWheel={onWheel}
      >
        {width > 0 && (
          <>
            {picket != null && (
              <rect
                x={x(picket) - 7}
                y={4}
                width={14}
                height={baseY - 2}
                rx={4}
                fill="var(--row-selected)"
                stroke="var(--brand-nav-marker)"
              />
            )}
            <line x1={PAD} y1={baseY} x2={width - PAD} y2={baseY} stroke="var(--border-strong)" />
            {ticks.map((pk) => (
              <g key={pk} pointer-events="none">
                <line
                  x1={x(pk)}
                  y1={baseY}
                  x2={x(pk)}
                  y2={baseY + 5}
                  stroke="var(--border-strong)"
                />
                <text
                  x={x(pk)}
                  y={baseY + 18}
                  font-size="11"
                  text-anchor="middle"
                  fill="var(--text-muted)"
                >
                  ПК {pk}
                </text>
              </g>
            ))}
            {stacks
              .filter(([pk]) => pk >= lo + v0 && pk <= lo + v1)
              .map(([pk, v]) => (
                <g
                  key={pk}
                  data-picket={pk}
                  data-selected={pk === picket ? '' : undefined}
                  style="cursor:pointer"
                  onClick={() => {
                    setPicket(pk)
                    setOpen(null)
                  }}
                >
                  <title>
                    ПК{pk}: {v.length} датч., высокий риск —{' '}
                    {v.filter((s) => s.level === 'high').length}
                  </title>
                  {/* Цель клика — вся стопка, а не точка в 6 px. */}
                  <rect
                    x={x(pk) - 5}
                    y={baseY - v.length * STEP - 6}
                    width={10}
                    height={v.length * STEP + 6}
                    fill="transparent"
                  />
                  {v.map((s, i) => (
                    <Dot key={s.channel_id} s={s} cx={x(pk)} cy={baseY - 5 - i * STEP} r={2.6} />
                  ))}
                </g>
              ))}
          </>
        )}
      </svg>

      <div
        class="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm"
        style="color:var(--text-secondary)"
      >
        {ORDER.map((l) => (
          <span key={l} class="flex items-center gap-2">
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
              <Dot s={{ level: l, reasons: [] }} cx={7} cy={7} r={4} />
            </svg>
            {LEVELS[l].label}
          </span>
        ))}
        <span class="flex items-center gap-2">
          <svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18">
            <Dot
              s={{ level: 'normal', reasons: [{ kind: 'plan', text: '', weight: 0 }] }}
              cx={9}
              cy={9}
              r={4}
            />
          </svg>
          снят по графику ППР — не отказ
        </span>
      </div>

      {picket != null && (
        <div class="flex flex-col gap-2">
          <h3 class="text-sm font-semibold" data-testid="sensor-picket">
            ПК{picket}: {list.length} датчиков
          </h3>
          <ul class="flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
            {list.map((s) => {
              const expanded = open === s.channel_id
              const plan = s.reasons.filter((r) => r.kind === 'plan')
              return (
                <li
                  key={s.channel_id}
                  data-channel-id={s.channel_id}
                  class="rounded"
                  style={`background:var(--bg-surface); border:1px solid var(--border-subtle); border-left:4px solid ${LEVELS[s.level].border}`}
                >
                  <button
                    type="button"
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : s.channel_id)}
                    class="w-full text-left px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1"
                  >
                    <span class="font-semibold" style="color:var(--text-primary)">
                      {s.name}
                    </span>
                    <span class="text-xs" style="color:var(--text-muted)">
                      {s.sensor_kind} · канал {s.channel_id}
                    </span>
                    <span class="ml-auto flex items-center gap-2">
                      <Badge level={s.level} />
                      <span class="tabular-nums text-sm" style="color:var(--text-primary)">
                        {s.score.toFixed(2)}
                      </span>
                    </span>
                  </button>
                  {/* Плановая причина видна и в свёрнутой строке: это главный момент демо. */}
                  {plan.map((r) => (
                    <p
                      key={r.text}
                      data-kind="plan"
                      class="mx-3 mb-2 px-2 py-1 rounded text-sm flex gap-2 items-start"
                      style="background:var(--row-selected); color:var(--text-primary); border-left:3px solid var(--state-info)"
                    >
                      <svg
                        aria-hidden="true"
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="var(--state-info)"
                        stroke-width="2"
                        stroke-linecap="round"
                        class="shrink-0 mt-0.5"
                      >
                        {/* ti-calendar (Tabler Icons, MIT) */}
                        <path d="M4 7a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12z" />
                        <path d="M16 3v4M8 3v4M4 11h16" />
                      </svg>
                      <span>
                        <strong>ППР:</strong> {r.text}
                      </span>
                    </p>
                  ))}
                  {expanded && <Details s={s} />}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}

function Details({ s }: { s: Sensor }) {
  const e = s.equipment
  const rest = s.reasons.filter((r) => r.kind !== 'plan')
  return (
    <div class="px-3 pb-3 flex flex-col gap-2 text-sm" style="color:var(--text-secondary)">
      {rest.length > 0 && (
        <ul class="flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
          {rest.map((r) => (
            <li key={r.text} data-kind={r.kind} class="flex items-center gap-2">
              <span class="tabular-nums w-12 shrink-0 text-right" style="color:var(--text-primary)">
                +{r.weight.toFixed(2)}
              </span>
              <span
                aria-hidden="true"
                class="h-2 rounded shrink-0"
                style={`width:${Math.round(r.weight * 120)}px; background:${r.kind === 'synthetic' ? 'var(--border-strong)' : LEVELS[s.level].border}`}
              />
              <span style="color:var(--text-primary)">{r.text}</span>
              {r.kind === 'synthetic' && (
                <span
                  class="text-xs rounded px-1.5"
                  style="border:1px dashed var(--border-strong); color:var(--text-muted)"
                >
                  синтетика
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {e ? (
        <dl class="grid gap-x-4 gap-y-0.5" style="grid-template-columns:max-content 1fr">
          <dt>Паспорт (синтетика)</dt>
          <dd style="color:var(--text-primary)">
            {e.manufacturer} {e.model_no}, № {e.equipment_no}
          </dd>
          <dt>В работе с</dt>
          <dd style="color:var(--text-primary)">
            {formatDate(e.in_service_from)}, срок службы {e.service_life_years} лет
          </dd>
          <dt>Последняя поверка</dt>
          <dd style="color:var(--text-primary)">
            {e.last_check_at
              ? `${formatDate(e.last_check_at)}, ${e.last_check_ok === false ? 'не прошёл' : e.last_check_ok ? 'годен' : 'итог не записан'}`
              : 'нет данных'}
          </dd>
        </dl>
      ) : (
        <p>Паспорта оборудования нет.</p>
      )}
      {s.section_id != null && (
        <a href={`/objects/${s.section_id}`} style="color:var(--link)" class="underline">
          Карточка участка ПК{s.picket} — история отказов канала
        </a>
      )}
    </div>
  )
}
