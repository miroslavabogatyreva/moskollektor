import { useEffect, useMemo, useState } from 'preact/hooks'
import { errorMessage } from '../../lib/format'
import { AxisLine, RiskMark } from './AxisLine'
import { DEFAULT_FILTERS, matchesFilters, type MapFilterState } from './filters'
import { MapFilters } from './MapFilters'
import { riskLabel, type RiskClass } from './risk'
import type { RiskClassRow, Section } from './types'
import type { ViewRange } from './viewport'

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
   каждая со своим масштабом (5.15, MOS-126, арифметика — в viewport.ts). */

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
  // Своё окно просмотра на каждую линию (префикс), не одно на коллектор.
  const [viewRanges, setViewRanges] = useState<Record<string, ViewRange | null>>({})
  const [risks, setRisks] = useState<RiskClassRow[]>([])
  const [filters, setFilters] = useState<MapFilterState>(DEFAULT_FILTERS)

  useEffect(() => {
    fetch('/api/objects', { headers: { 'X-User-Login': API_LOGIN } })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Section[]>
      })
      .then((data) => {
        data.sort(
          (a, b) =>
            (a.collector ?? Infinity) - (b.collector ?? Infinity) ||
            a.picket - b.picket ||
            a.section_id - b.section_id,
        )
        setSections(data)
        setCollector(data.find((s) => s.mapping_status === 'resolved')?.collector ?? null)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  useEffect(() => {
    const refresh = () =>
      fetch('/api/risks', { headers: { 'X-User-Login': API_LOGIN } })
        .then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
          return r.json() as Promise<RiskClassRow[]>
        })
        .then(setRisks)
        // Риск не грузится — не блокируем схему, участки просто выйдут нейтральными.
        .catch((e) => console.error('не удалось загрузить /api/risks:', errorMessage(e)))
    refresh()
    const timer = setInterval(refresh, 60_000)
    return () => clearInterval(timer)
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
      if (s.mapping_status !== 'resolved' || s.collector == null) continue
      const g = byId.get(s.collector) ?? { name: s.collector_name ?? String(s.collector), count: 0 }
      g.count += 1
      byId.set(s.collector, g)
    }
    return [...byId.entries()].sort((a, b) => a[0] - b[0])
  }, [sections])

  const collectorName =
    collectors.find(([c]) => c === collector)?.[1].name ?? String(collector ?? '')

  const onAxis = useMemo(
    () =>
      sections && collector != null
        ? sections.filter((s) => s.mapping_status === 'resolved' && s.collector === collector)
        : [],
    [sections, collector],
  )

  const unresolved = sections?.filter((s) => s.mapping_status !== 'resolved') ?? []

  // Фильтры сужают набор значков, но не масштаб оси — линейка и подписи ПК держатся
  // на полном onAxis, иначе включённый фильтр менял бы диапазон под ногами.
  const filteredAxis = useMemo(
    () => onAxis.filter((s) => matchesFilters(s, riskBySection.get(s.section_id), filters)),
    [onAxis, riskBySection, filters],
  )

  // Смена коллектора меняет набор линий — старые окна просмотра теряют смысл.
  useEffect(() => setViewRanges({}), [collector])

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
                  {g.name} · ID {c} · {g.count} участков
                </option>
              ))}
            </select>
          </label>

          <p class="text-sm" style="color:var(--text-secondary)">
            {collectorName} · ID дерева объектов {collector}. Ключ участка — исторический префикс
            тега:пикет; префикс не является номером коллектора. Вероятность относится к коллектору;
            локальный риск участка не оценён.
          </p>
          {unresolved.length > 0 && (
            <aside class="text-sm" style="color:var(--text-secondary)">
              <p>
                Не размещено участков: {unresolved.length}. Привязка к одному коллектору не
                подтверждена.
              </p>
              <ul>
                {unresolved.map((s) => (
                  <li key={s.section_id}>
                    <a href={`/objects/${s.section_id}`}>Участок {s.smvu_key}</a>
                    {' · '}
                    {s.mapping_status === 'ambiguous'
                      ? `несколько коллекторов: ${(s.collector_ids ?? []).join(', ')}`
                      : 'нет полной привязки'}
                  </li>
                ))}
              </ul>
            </aside>
          )}

          <MapFilters
            filters={filters}
            onChange={setFilters}
            matchCount={filteredAxis.length}
            totalCount={onAxis.length}
          />

          <p class="text-sm" style="color:var(--text-secondary)">
            Коллектор «{collectorName}»: {lines.length} {lines.length === 1 ? 'линия' : 'линии'}
          </p>

          <div class="flex flex-col gap-4">
            {lines.map(([prefix, all]) => (
              <AxisLine
                key={prefix}
                prefix={prefix}
                all={all}
                visible={filteredByPrefix.get(prefix) ?? []}
                riskBySection={riskBySection}
                viewRange={viewRanges[prefix] ?? null}
                onViewRangeChange={(v) => setViewRanges((prev) => ({ ...prev, [prefix]: v }))}
              />
            ))}
          </div>

          {/* Легенда состояний (MOS-170): названия рядом с цветом, не только
              в title значка — на настенном экране диспетчерской мышью не водят. */}
          <div
            class="flex flex-wrap items-center gap-4 text-sm"
            style="color:var(--text-secondary)"
          >
            {LEGEND_STATES.map((cls) => (
              <span key={String(cls)} class="flex items-center gap-1.5">
                <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
                  <RiskMark cls={cls} cx={6} cy={6} size={10} />
                </svg>
                {riskLabel(cls)}
                {cls == null && (
                  <span style="color:var(--text-muted)"> — расчёта по объекту не было</span>
                )}
              </span>
            ))}
          </div>
        </>
      )}
    </main>
  )
}
