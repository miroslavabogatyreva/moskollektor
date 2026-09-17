import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import type { Section } from './types'

/* Ось пикетов — первая половина задачи 5.3 (MOS-50): сам чертёж и клик по метке
   ведёт на /objects/:sectionId (ObjectCard, 5.5, MOS-52). Цвет и форма метки
   по уровню риска — вторая половина, оставлена без изменений в этой правке.
   3 173 участка одной лентой не показать (dashboard/dashboard.md, разд. 9) —
   поэтому сначала выбор коллектора, потом ось. Заголовок экрана отличается
   от подписи в меню: меню держится за формулировку М-05 ("Карта объектов"),
   а здесь — про способ показа. */

const AXIS_WIDTH = 1000
const AXIS_HEIGHT = 120
const PADDING = 40

export function MapScreen(_props: Record<string, unknown>) {
  const [sections, setSections] = useState<Section[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collector, setCollector] = useState<number | null>(null)

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

  const maxPicket = Math.max(1, ...onAxis.map((s) => s.picket))
  const innerWidth = AXIS_WIDTH - PADDING * 2
  const x = (picket: number) => PADDING + (picket / maxPicket) * innerWidth
  const baselineY = AXIS_HEIGHT / 2

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

          <svg
            viewBox={`0 0 ${AXIS_WIDTH} ${AXIS_HEIGHT}`}
            role="img"
            aria-label={`Ось пикетов коллектора ${collector}, ${onAxis.length} участков`}
            class="w-full"
            style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px"
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
              ПК0
            </text>
            <text
              x={AXIS_WIDTH - PADDING}
              y={baselineY + 28}
              font-size="11"
              fill="var(--text-muted)"
              text-anchor="end"
            >
              ПК{maxPicket}
            </text>
            {onAxis.map((s) => (
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
