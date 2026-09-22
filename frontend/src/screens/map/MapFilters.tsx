// Три выпадающих списка над осью пикетов — MOS-122, задача 5.12, приёмка Ф-93.
// «Район» — не выпадающий список с выбором, а строка: у дерева объектов заказчика
// один корень на весь парк («Район по эксплуатации», docs/day-one.md:187), различать
// нечего. Показываем это значение как факт, а не как фильтр с одним пунктом —
// выпадающий список, который нечего выбирать, был бы декорацией.

import {
  KIND_FILTER_OPTIONS,
  RISK_FILTER_OPTIONS,
  type KindFilter,
  type MapFilterState,
  type RiskFilter,
} from './filters'

const selectStyle =
  'text-sm px-2 py-1 rounded background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

interface Props {
  filters: MapFilterState
  onChange: (filters: MapFilterState) => void
  matchCount: number
  totalCount: number
}

export function MapFilters({ filters, onChange, matchCount, totalCount }: Props) {
  return (
    <div class="flex flex-wrap items-center gap-4">
      <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
        Уровень риска
        <select
          class="text-sm px-2 py-1 rounded"
          style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          value={filters.risk}
          onChange={(e) =>
            onChange({ ...filters, risk: (e.target as HTMLSelectElement).value as RiskFilter })
          }
        >
          {RISK_FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)">
        Тип объекта
        <select
          class="text-sm px-2 py-1 rounded"
          style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          value={filters.kind}
          onChange={(e) =>
            onChange({ ...filters, kind: (e.target as HTMLSelectElement).value as KindFilter })
          }
        >
          {KIND_FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <span class="text-sm" style="color:var(--text-secondary)">
        Район: Район по эксплуатации
      </span>

      <span class="text-sm num" style="color:var(--text-muted)">
        {matchCount} из {totalCount} участков
      </span>
    </div>
  )
}
