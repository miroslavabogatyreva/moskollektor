// Три фильтра экрана карты — MOS-122, задача 5.12, приёмка Ф-93. Чистая логика
// отбора, отдельно от MapFilters.tsx (разметка) и index.tsx (состояние) — проверяется
// node-скриптом, как risk.ts/risk.selfcheck.ts.
//
// «Район» в списке нет: дерево объектов заказчика (smvu.object_tree) имеет один
// корень на весь парк, «Район по эксплуатации» (docs/day-one.md:187) — различать
// там нечего, третий фильтр из Ф-93 не построить на настоящих данных.

import type { RiskClass } from './risk'
import type { ObjectKind, Section } from './types'

export type RiskFilter = 'all' | 'high' | 'normal'
export type KindFilter = 'all' | ObjectKind

export interface MapFilterState {
  risk: RiskFilter
  kind: KindFilter
}

export const DEFAULT_FILTERS: MapFilterState = { risk: 'all', kind: 'all' }

export const RISK_FILTER_OPTIONS: { value: RiskFilter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'high', label: 'Высокий' },
  { value: 'normal', label: 'Низкий' },
]

export const KIND_FILTER_OPTIONS: { value: KindFilter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'controlHouse', label: 'Диспетчерский дом' },
  { value: 'guardObject', label: 'Охраняемый объект' },
]

export function matchesFilters(
  section: Section,
  riskClass: RiskClass,
  filters: MapFilterState,
): boolean {
  if (filters.risk !== 'all' && riskClass !== filters.risk) return false
  // sections.json на стенде и в новой сборке едут порознь (деплой не атомарен):
  // пока рассинхрон не выкатили, старый файл отдаёт участки без kinds вовсе —
  // ?? [] не даёт .includes упасть на секцию из старого файла (MOS-122, находка ab).
  if (filters.kind !== 'all' && !(section.kinds ?? []).includes(filters.kind)) return false
  return true
}
