import { FastTip } from '../../components/FastTip'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { SENSOR_LEVELS, SensorBadge } from '../../components/SensorBadge'
import { SyntheticToggle } from '../../components/SyntheticToggle'
import { apiFetch } from '../../lib/api'
import { errorMessage, formatDate, formatDateTime } from '../../lib/format'
import { usePoll } from '../../lib/poll'
import { датчиков, линий } from '../../lib/plural'
import {
  БЕЗ_ЛИНИИ,
  доляПричины,
  процент,
  линииДатчиков,
  sensorRiskUrl,
  срезРасчёта,
  type SensorLevel,
  type SensorRiskPage,
  type SensorRow,
} from '../../lib/sensorRisk'
import { tickStep } from './AxisLine'
import type { Section } from './types'
import { fullView, isFullView, panView, zoomView, type ViewRange } from './viewport'

/* Прогноз по датчикам на выбранном коллекторе — SL.6 (MOS-255), эпик MOS-248.
   Начинался демо на одном узле «объект Каппа ДУ» (SL.0, MOS-249), теперь стоит
   под схемой любого из 16 коллекторов. Балл и причины считает
   GET /api/sensor-risk?collector=<id>; паспорта оборудования синтетические
   (docs/sensor-level-proposal.md), история отказов реальная — об этом плашка
   переключателя. Переключатель синтетики живёт в адресе (?synthetic=0).

   Полоса — одна на линию-префикс тега, как ось AxisLine.tsx: у «объекта Мю»
   две линии (914 и 915), и пикет 0 у каждой свой — на общей полосе датчики
   разных линий легли бы друг на друга. Каждый датчик — точка на своём пикете,
   датчики одного пикета стопкой. В стопке не больше НА_ПИКЕТЕ самых рискованных
   значков, остальные — числом «+N» над ней: на ПК632 коллектора «объект Каппа» 100
   датчиков, и 100 кружков в 140 px сливались в сплошную колонну. Все датчики пикета —
   в списке под схемой.

   С MOS-265 (главная — эта схема) значками рисуются только «высокий» и «наблюдать»
   плюс кольца ППР: на Гамме 1 075 датчиков из 1 081 в норме, и серые кружки
   прятали шесть рискованных. Пикет, где все в норме, — серый штрих на оси; при
   масштабе от ПОДПИСЬ_ОТ px на пикет рядом подпись «N в норме». «+N» — только
   рискованные, не влезшие в стопку.

   /map?channel=<id> выбирает коллектор (index.tsx), здесь — линию и пикет
   датчика, приближает полосу к пикету и раскрывает строку датчика. */

// Сколько значков рисуем в стопке пикета: красные и жёлтые сверху, остальное — «+N».
const НА_ПИКЕТЕ = 8
// С какого масштаба (px на пикет) у стопки серое число датчиков в норме, и с какого —
// подпись целиком «N в норме»: при 9 px шрифта она шириной около 50 px и уже налезает
// на соседний пикет.
const ПОДПИСЬ_ОТ = 24
const ПОДПИСЬ_ЦЕЛИКОМ_ОТ = 60

// «объект Каппа ДУ» — узел демо SL.0; /map?demo=sensors открывает его коллектор.
export const DEMO_NODE = 5657

// Все датчики коллектора одним запросом: на самом плотном, «объекте Мю», их 1 487.
const ЛИМИТ = 5000
const ORDER: SensorLevel[] = ['high', 'watch', 'normal']
const PAD = 24
const BASE_GAP = 34 // от оси до низа чертежа: деления и подписи ПК

// На ось пикетов встают только датчики с пикетом; у канала охранной зоны или
// здания ДП пикета нет (picket null, миграция 031) — их считаем строкой под схемой.
type ПоПикету = SensorRow & { picket: number }

interface Выбор {
  prefix: string
  picket: number
}

const поППР = (s: Pick<SensorRow, 'reasons'>) => s.reasons.some((x) => x.kind === 'plan')

// Значок датчика: high — треугольник, watch — ромб, normal — круг (форма читается
// без цвета, как на оси). Датчик, снятый по графику ППР, обведён кольцом --state-info.
// ringOnly — на оси у датчика в норме под ППР рисуем одно кольцо, без круга.
function Dot({
  s,
  cx,
  cy,
  r,
  ringOnly,
}: {
  s: Pick<SensorRow, 'level' | 'reasons'>
  cx: number
  cy: number
  r: number
  ringOnly?: boolean
}) {
  const c = SENSOR_LEVELS[s.level]
  const p = { fill: c.fill, stroke: c.border, 'stroke-width': 1, 'data-level': s.level }
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
      {!ringOnly && shape}
      {поППР(s) && (
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

export function SensorDemo({
  collector,
  collectorName,
  sections,
  synthetic,
  channel,
  scrollTo,
  node,
}: {
  collector: number
  collectorName: string
  // Участки коллектора — чтобы разложить датчики по линиям (section_id → префикс).
  sections: Section[]
  synthetic: boolean
  // Датчик из адреса /map?channel=<id>.
  channel?: number
  scrollTo?: boolean
  // Узел дерева объектов слева («Шкаф ОПС объект Вита»): только датчики его участков.
  node?: { name: string; sections: Set<number> } | null
}) {
  const [data, setData] = useState<SensorRiskPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pick, setPick] = useState<Выбор | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  // Датчики «в норме» без плана ППР свёрнуты: на ПК632 коллектора «объект Каппа» их 94
  // из 100, и список растягивал страницу на 5 000 px. Новый пикет — снова свёрнуто.
  const [allNormal, setAllNormal] = useState(false)
  useEffect(() => setAllNormal(false), [pick?.prefix, pick?.picket])
  const [views, setViews] = useState<Record<string, ViewRange | null>>({})
  const rootRef = useRef<HTMLElement>(null)
  // Датчик из адреса применяем один раз: иначе опрос раз в минуту возвращал бы
  // выбор и масштаб к нему, пока диспетчер смотрит другой пикет.
  const применён = useRef<number | undefined>(undefined)

  // Раз в минуту от общего опроса (НФ-89), как риски на оси. Смена коллектора
  // гасит старый ответ сразу — чужие датчики на новой схеме хуже пустоты.
  const { tick } = usePoll()
  useEffect(() => {
    setData(null)
    setError(null)
    setPick(null)
    setViews({})
    применён.current = undefined
  }, [collector])
  useEffect(() => {
    let отменено = false
    apiFetch(sensorRiskUrl({ synthetic, collector, limit: ЛИМИТ }))
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
  }, [collector, synthetic, tick])

  const ключУчастка = useMemo(
    () => new Map(sections.map((s) => [s.section_id, s.smvu_key])),
    [sections],
  )
  // До 28.09.2026 узел дерева отбирал только ось «по участкам», а здесь не менялось ничего.
  const датчики = useMemo(
    () =>
      node
        ? (data?.items ?? []).filter((s) => s.section_id != null && node.sections.has(s.section_id))
        : (data?.items ?? []),
    [data, node],
  )
  const наОси = useMemo(() => датчики.filter((s): s is ПоПикету => s.picket != null), [датчики])
  const безПикета = датчики.length - наОси.length
  const lines = useMemo(() => линииДатчиков(наОси, ключУчастка), [наОси, ключУчастка])
  const prefixOf = (id: number) => lines.find(([, v]) => v.some((s) => s.channel_id === id))?.[0]

  // Выбор после ответа: датчик из адреса; иначе прошлый выбор, если он ещё есть
  // (переключили синтетику — пикет остаётся); иначе пикет самого рискованного
  // датчика (items отсортированы сервером по баллу).
  useEffect(() => {
    if (!data) return
    const изАдреса = channel != null ? наОси.find((s) => s.channel_id === channel) : undefined
    if (изАдреса && применён.current !== channel) {
      применён.current = channel
      const prefix = prefixOf(изАдреса.channel_id)!
      setPick({ prefix, picket: изАдреса.picket })
      setOpen(изАдреса.channel_id)
      // Приближаем линию к пикету датчика — десятая часть линии, как у участка из адреса.
      const pk = lines.find(([p]) => p === prefix)![1].map((s) => s.picket)
      const lo = Math.min(...pk)
      const max = Math.max(1, Math.max(...pk) - lo)
      setViews((v) => ({
        ...v,
        [prefix]: zoomView(fullView(max), 0.1, изАдреса.picket - lo, max, 5),
      }))
      return
    }
    if (
      pick &&
      lines.some(([p, v]) => p === pick.prefix && v.some((s) => s.picket === pick.picket))
    )
      return
    const top = наОси[0]
    setPick(top ? { prefix: prefixOf(top.channel_id)!, picket: top.picket } : null)
    setOpen(top?.channel_id ?? null)
  }, [data, channel, node])

  useEffect(() => {
    if (!scrollTo || !data) return
    const li = open != null && rootRef.current?.querySelector(`li[data-channel-id="${open}"]`)
    ;(li || rootRef.current)?.scrollIntoView({ block: li ? 'center' : 'start' })
  }, [scrollTo, data == null, channel])

  if (error)
    return (
      <p class="text-sm" style="color:var(--state-error)">
        Прогноз по датчикам не загрузился: {error}
      </p>
    )

  const list = pick
    ? (lines.find(([p]) => p === pick.prefix)?.[1] ?? [])
        .filter((s) => s.picket === pick.picket)
        .sort((a, b) => ORDER.indexOf(a.level) - ORDER.indexOf(b.level) || b.score - a.score)
    : []
  const важный = (s: SensorRow) =>
    s.level !== 'normal' || s.channel_id === open || s.reasons.some((r) => r.kind === 'plan')
  const shownList = allNormal ? list : list.filter(важный)
  const свёрнуто = list.length - shownList.length
  const count = (l: SensorLevel) => датчики.filter((s) => s.level === l).length

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
          Прогноз по датчикам · {collectorName}
          {node && ` · ${node.name}`}
        </h2>
        {data && (
          <span class="text-sm" style="color:var(--text-secondary)">
            {срезРасчёта(data.as_of, formatDateTime)} · {датчиков(датчики.length)}: высокий риск —{' '}
            {count('high')}, наблюдать — {count('watch')}, норма — {count('normal')}
          </span>
        )}
      </div>

      <SyntheticToggle on={synthetic} />

      {!data ? (
        <p style="color:var(--text-muted)">Загрузка прогноза по датчикам…</p>
      ) : датчики.length === 0 ? (
        <p class="text-sm" style="color:var(--text-muted)">
          {node
            ? 'У этого узла нет датчиков с прогнозом.'
            : 'На коллекторе нет датчиков с прогнозом.'}
        </p>
      ) : (
        <>
          <p class="text-sm" style="color:var(--text-secondary)">
            {линий(lines.length)} · нажмите на стопку, чтобы открыть датчики пикета
            {безПикета > 0 && ` · без пикета (охранная зона, здание) ${безПикета} — на оси их нет`}
          </p>
          {lines.map(([prefix, items]) => (
            <SensorLine
              key={prefix}
              prefix={prefix}
              items={items}
              picket={pick?.prefix === prefix ? pick.picket : null}
              view={views[prefix] ?? null}
              onView={(v) => setViews((prev) => ({ ...prev, [prefix]: v }))}
              onPick={(pk) => {
                setPick({ prefix, picket: pk })
                setOpen(null)
              }}
            />
          ))}
          <Legend />
        </>
      )}

      {pick && list.length > 0 && (
        <div class="flex flex-col gap-2">
          <h3 class="text-sm font-semibold" data-testid="sensor-picket">
            {pick.prefix === БЕЗ_ЛИНИИ ? '' : `Линия ${pick.prefix}, `}ПК{pick.picket}:{' '}
            {датчиков(list.length)}
          </h3>
          <ul class="flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
            {shownList.map((s) => (
              <SensorItem
                key={s.channel_id}
                s={s}
                expanded={open === s.channel_id}
                onToggle={() => setOpen(open === s.channel_id ? null : s.channel_id)}
              />
            ))}
          </ul>
          {свёрнуто > 0 && (
            <button
              type="button"
              class="self-start text-sm font-semibold underline underline-offset-2"
              style="color:var(--link)"
              onClick={() => setAllNormal(true)}
            >
              Показать ещё {свёрнуто} в норме
            </button>
          )}
        </div>
      )}
    </section>
  )
}

// Одна полоса — одна линия-префикс со своим масштабом и своими кнопками,
// та же арифметика окна, что у оси (viewport.ts). viewport.ts считает окно
// от 0 до max — работаем в сдвиге от первого пикета линии.
function SensorLine({
  prefix,
  items,
  picket,
  view,
  onView,
  onPick,
}: {
  prefix: string
  items: ПоПикету[]
  picket: number | null
  view: ViewRange | null
  onView: (v: ViewRange | null) => void
  onPick: (picket: number) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = svgRef.current
    if (!el) return
    setWidth(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Датчики по пикетам. В стопке снизу вверх: кольца ППР датчиков в норме, потом
  // рискованные: watch, потом high, внутри уровня по возрастанию балла — красные наверху; не больше НА_ПИКЕТЕ, кольца
  // уступают место риску. Датчики в норме — числом normal.
  const stacks = useMemo(() => {
    const m = new Map<number, SensorRow[]>()
    for (const s of items) m.set(s.picket, [...(m.get(s.picket) ?? []), s])
    return [...m.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([pk, v]) => {
        const риск = v
          .filter((s) => s.level !== 'normal')
          .sort((a, b) => ORDER.indexOf(b.level) - ORDER.indexOf(a.level) || a.score - b.score)
        const ппр = v.filter((s) => s.level === 'normal' && поППР(s))
        const shown = [...ппр, ...риск].slice(-НА_ПИКЕТЕ)
        const hidden = риск.length - shown.filter((s) => s.level !== 'normal').length
        return { pk, all: v, shown, hidden, normal: v.length - риск.length }
      })
  }, [items])

  const lo = stacks[0]?.pk ?? 0
  const max = Math.max(1, (stacks.at(-1)?.pk ?? 1) - lo)
  const [v0, v1] = view ?? [0, max]
  const inner = Math.max(1, width - PAD * 2)
  const x = (pk: number) => PAD + ((pk - lo - v0) / (v1 - v0)) * inner
  const подпись = inner / (v1 - v0) >= ПОДПИСЬ_ОТ
  const целиком = inner / (v1 - v0) >= ПОДПИСЬ_ЦЕЛИКОМ_ОТ
  // Под подпись «N в норме» стопка поднимается на 12 px.
  const под = подпись ? 12 : 0
  const tallest = Math.max(0, ...stacks.map((st) => st.shown.length))
  const step = 7
  const baseY = 22 + под + Math.max(1, tallest * step)
  const height = baseY + BASE_GAP
  const tick = tickStep((v1 - v0) / inner)
  const ticks: number[] = []
  for (let pk = Math.ceil((lo + v0) / tick) * tick; pk <= lo + v1; pk += tick) ticks.push(pk)
  const zoomed = !isFullView([v0, v1], max)
  const zoom = (f: number, c = picket != null ? picket - lo : (v0 + v1) / 2) =>
    onView(zoomView([v0, v1], f, c, max, 5))
  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    zoom(e.deltaY > 0 ? 1.4 : 1 / 1.4, v0 + ((e.clientX - rect.left - PAD) / inner) * (v1 - v0))
  }
  const кнопки: [string, boolean, () => void][] = [
    ['+ приблизить', v1 - v0 <= 5, () => zoom(0.5)],
    ['− отдалить', !zoomed, () => zoom(2)],
    ['◀ левее', !zoomed || v0 <= 0, () => onView(panView([v0, v1], -0.3, max))],
    ['правее ▶', !zoomed || v1 >= max, () => onView(panView([v0, v1], 0.3, max))],
    ['вся линия', !zoomed, () => onView(null)],
  ]
  const high = items.filter((s) => s.level === 'high').length
  const имя = prefix === БЕЗ_ЛИНИИ ? 'Без участка' : `Линия ${prefix}`

  return (
    <div data-line={prefix} class="flex flex-col gap-1">
      <div class="flex items-center justify-between gap-x-4 gap-y-2 text-sm flex-wrap">
        <span style="color:var(--text-secondary)">
          <strong style="color:var(--text-primary)">{имя}</strong>, ПК{Math.round(lo + v0)}–ПК
          {Math.round(lo + v1)} · {датчиков(items.length)}, высокий риск — {high}
        </span>
        <div
          role="group"
          aria-label={`Масштаб полосы датчиков, ${имя}`}
          class="inline-flex flex-wrap rounded-md overflow-hidden"
          style="border:1px solid var(--accent-border)"
        >
          {кнопки.map(([надпись, выкл, действие], i) => (
            <button
              key={надпись}
              type="button"
              class="px-3 py-1 text-sm font-semibold bg-[var(--bg-surface)] enabled:hover:bg-[var(--accent-tint)] disabled:opacity-40"
              style={`color:var(--accent-text)${i > 0 ? '; border-left:1px solid var(--accent-border)' : ''}`}
              disabled={выкл}
              onClick={действие}
            >
              {надпись}
            </button>
          ))}
        </div>
      </div>

      <FastTip>
        <svg
          ref={svgRef}
          role="img"
          aria-label={`Датчики, ${имя}, по пикетам`}
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
                .filter(({ pk }) => pk >= lo + v0 && pk <= lo + v1)
                .map(({ pk, all, shown, hidden, normal }) => {
                  const низ = baseY - 5 - (normal > 0 ? под : 0)
                  const верх = shown.length
                    ? низ - (shown.length - 1) * step - 5 - (hidden ? 12 : 0)
                    : baseY - 6 - (normal > 0 ? под : 0)
                  return (
                    <g
                      key={pk}
                      data-picket={pk}
                      // Число в норме — и на стопке, где подписи при мелком масштабе нет.
                      data-normal={normal}
                      data-selected={pk === picket ? '' : undefined}
                      style="cursor:pointer"
                      onClick={() => onPick(pk)}
                    >
                      {/* Цель клика — вся стопка, а не точка в 6 px. Подсказка — внутри
                      неё, а не прямым ребёнком <g>: `g > desc` на оси считает метки
                      участков (US-05 сц. 6), датчики туда попадать не должны. */}
                      <rect
                        x={x(pk) - 5}
                        y={верх - 2}
                        width={10}
                        height={baseY - верх + 2}
                        fill="transparent"
                      >
                        <desc>
                          ПК{pk}: {датчиков(all.length)}, высокий риск —{' '}
                          {all.filter((s) => s.level === 'high').length}, в норме — {normal}
                        </desc>
                      </rect>
                      {/* Пикет, где все в норме, — серый штрих 1×6 px на оси. */}
                      {shown.length === 0 && (
                        <rect
                          x={x(pk) - 0.5}
                          y={baseY - 6}
                          width={1}
                          height={6}
                          fill="var(--text-muted)"
                        />
                      )}
                      {shown.map((s, i) => (
                        <Dot
                          key={s.channel_id}
                          s={s}
                          cx={x(pk)}
                          cy={низ - i * step}
                          r={2.6}
                          ringOnly={s.level === 'normal'}
                        />
                      ))}
                      {hidden > 0 && (
                        <text
                          data-hidden={hidden}
                          x={x(pk)}
                          y={низ - (shown.length - 1) * step - 8}
                          font-size="10"
                          text-anchor="middle"
                          fill="var(--text-muted)"
                        >
                          +{hidden}
                        </text>
                      )}
                      {подпись && normal > 0 && (
                        <text
                          data-normal={normal}
                          x={x(pk)}
                          y={baseY - (shown.length ? 3 : 9)}
                          font-size="9"
                          text-anchor="middle"
                          fill="var(--text-muted)"
                        >
                          {целиком ? `${normal} в норме` : normal}
                        </text>
                      )}
                    </g>
                  )
                })}
            </>
          )}
        </svg>
      </FastTip>
    </div>
  )
}

function Legend() {
  return (
    // Список, а не ряд <span>: легенду оси US-05 сц. 5 ищет как последний span
    // со значком и словом «высокий риск» — легенда датчиков стоит ниже и не должна
    // подменять её. Да и перечень условных знаков — это список.
    <ul
      aria-label="Условные знаки датчиков"
      class="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm"
      style="color:var(--text-secondary); list-style:none; padding:0; margin:0"
    >
      {ORDER.map((l) => (
        <li key={l} class="flex items-center gap-2">
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
            {l === 'normal' ? (
              <rect x={6.5} y={4} width={1} height={6} fill="var(--text-muted)" />
            ) : (
              <Dot s={{ level: l, reasons: [] }} cx={7} cy={7} r={4} />
            )}
          </svg>
          {SENSOR_LEVELS[l].label}
          {l === 'normal' && ' — штрих на оси, при приближении серое число у пикета'}
        </li>
      ))}
      <li class="flex items-center gap-2">
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18">
          <Dot
            s={{ level: 'normal', reasons: [{ kind: 'plan', text: '', weight: 0 }] }}
            cx={9}
            cy={9}
            r={4}
          />
        </svg>
        снят по графику ППР — не отказ
      </li>
    </ul>
  )
}

function SensorItem({
  s,
  expanded,
  onToggle,
}: {
  s: SensorRow
  expanded: boolean
  onToggle: () => void
}) {
  const plan = s.reasons.filter((r) => r.kind === 'plan')
  return (
    <li
      data-channel-id={s.channel_id}
      class="rounded"
      style={`background:var(--bg-surface); border:1px solid var(--border-subtle); border-left:4px solid ${SENSOR_LEVELS[s.level].border}`}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        class="w-full text-left px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1"
      >
        <span class="font-semibold" style="color:var(--text-primary)">
          {s.name}
        </span>
        <span class="text-xs" style="color:var(--text-muted)">
          {s.sensor_kind} · канал {s.channel_id}
        </span>
        <span class="ml-auto flex items-center gap-2">
          <SensorBadge level={s.level} />
          <span class="tabular-nums text-sm" style="color:var(--text-primary)">
            {процент(s.score)}
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
}

function Details({ s }: { s: SensorRow }) {
  const e = s.equipment
  const rest = s.reasons.filter((r) => r.kind !== 'plan')
  return (
    <div class="px-3 pb-3 flex flex-col gap-2 text-sm" style="color:var(--text-secondary)">
      {rest.length > 0 && (
        <ul class="flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
          {rest.map((r) => (
            // flex-wrap: на 390 px плашка «синтетика» за длинной причиной вылезала
            // за окно на 26 px (e2e mos-265-home, 28.09.2026) — теперь уходит строкой ниже.
            <li key={r.text} data-kind={r.kind} class="flex flex-wrap items-center gap-2">
              <span class="tabular-nums w-16 shrink-0 text-right" style="color:var(--text-primary)">
                +{процент(r.weight)}
              </span>
              <span
                aria-hidden="true"
                class="h-2 rounded shrink-0"
                style={`width:${Math.round(доляПричины(r.weight, s.score) * 120)}px; background:${r.kind === 'synthetic' ? 'var(--border-strong)' : SENSOR_LEVELS[s.level].border}`}
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
