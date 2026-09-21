import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import type { Section } from './types'
import { fullView, isFullView, panView, zoomView, type ViewRange } from './viewport'

/* Ось пикетов — первая половина задачи 5.3 (MOS-50): сам чертёж и клик по метке
   ведёт на /objects/:sectionId (ObjectCard, 5.5, MOS-52). Цвет и форма метки
   по уровню риска — вторая половина, оставлена без изменений в этой правке.
   3 173 участка одной лентой не показать (dashboard/dashboard.md, разд. 9) —
   поэтому сначала выбор коллектора, потом ось. Заголовок экрана отличается
   от подписи в меню: меню держится за формулировку М-05 ("Карта объектов"),
   а здесь — про способ показа.

   Масштаб (5.15, MOS-126): на коллекторе 15 из 74 пар соседних меток 49 стоят
   ближе 10 единиц SVG при диаметре метки 10 — на полной оси их не разлепить
   мышью. Приближение не перекладывает точки, а сужает видимый диапазон
   пикетов на той же ширине SVG — слипшиеся метки раздвигаются сами, без
   алгоритма разбежки. Арифметика окна — в viewport.ts, с самопроверкой. */

const AXIS_WIDTH = 1000
const AXIS_HEIGHT = 120
const PADDING = 40
const MIN_VIEW_FRACTION = 0.01

export function MapScreen(_props: Record<string, unknown>) {
  const [sections, setSections] = useState<Section[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collector, setCollector] = useState<number | null>(null)
  const [viewRange, setViewRange] = useState<ViewRange | null>(null)

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
      .catch((e) => setError(String(e)))
  }, [])

  const collectors = useMemo(() => {
    if (!sections) return []
    const counts = new Map<number, number>()
    for (const s of sections) counts.set(s.collector, (counts.get(s.collector) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => a[0] - b[0])
  }, [sections])

  const onAxis = useMemo(
    () => (sections && collector != null ? sections.filter((s) => s.collector === collector) : []),
    [sections, collector],
  )

  // Смена коллектора меняет диапазон пикетов — старое окно просмотра теряет смысл.
  useEffect(() => setViewRange(null), [collector])

  const maxPicket = Math.max(1, ...onAxis.map((s) => s.picket))
  const minViewWidth = Math.max(1, maxPicket * MIN_VIEW_FRACTION)
  const [viewStart, viewEnd] = viewRange ?? fullView(maxPicket)
  const viewWidth = viewEnd - viewStart
  const zoomed = !isFullView([viewStart, viewEnd], maxPicket)
  const visibleAxis = useMemo(
    () => onAxis.filter((s) => s.picket >= viewStart && s.picket <= viewEnd),
    [onAxis, viewStart, viewEnd],
  )

  const innerWidth = AXIS_WIDTH - PADDING * 2
  const x = (picket: number) => PADDING + ((picket - viewStart) / viewWidth) * innerWidth
  const baselineY = AXIS_HEIGHT / 2

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
              {collectors.map(([c, n]) => (
                <option key={c} value={c}>
                  {c} · {n} участков
                </option>
              ))}
            </select>
          </label>

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
            aria-label={`Ось пикетов коллектора ${collector}, показан участок ПК${Math.round(viewStart)}–ПК${Math.round(viewEnd)} из ${onAxis.length} участков`}
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
            {visibleAxis.map((s) => (
              <circle
                key={s.section_id}
                cx={x(s.picket)}
                cy={baselineY}
                r={5}
                fill="var(--bg-table)"
                stroke="var(--border-strong)"
                stroke-width={1.5}
                style="cursor:pointer"
                onClick={() => route(`/objects/${s.section_id}`)}
              >
                <title>{`${s.smvu_key} · участок ${s.section_id}`}</title>
              </circle>
            ))}
          </svg>
          <p class="text-xs" style="color:var(--text-muted)">
            Цвет и форма метки по уровню риска ещё не подключены к /api/risks — пока каждая метка
            нейтральная, это не значит «нет данных» в смысле молчащего датчика.
          </p>
        </>
      )}
    </main>
  )
}
