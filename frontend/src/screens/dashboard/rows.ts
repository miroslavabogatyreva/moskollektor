// Строка дашборда: имя объекта словами и уровень риска словом и цветом —
// MOS-127, задача 5.16, приёмка М-04. Чистая логика без DOM, проверяется
// node-скриптом (rows.selfcheck.ts), как lag.ts и risk.ts на соседнем экране.

import type { RiskClass } from './types'

// Authoritative customer-tree mapping shared with the collector map.
import type { Section } from '../map/types'
export type SectionRef = Section

export function имяОбъекта(section: SectionRef | undefined, sectionId: number): string {
  // Справочник не доехал или участка в нём нет — показываем номер, а не пустоту.
  // Пустая ячейка в столбце «Объект» читается как «объекта нет», а он есть.
  if (!section) return `Участок ${sectionId}`
  if (section.mapping_status !== 'resolved')
    return `Участок ${section.smvu_key} · привязка к коллектору не подтверждена`
  return `${section.collector_name ?? `Коллектор ${section.collector}`}, пикет ${section.picket}`
}

export function указатель(sections: SectionRef[]): Map<number, SectionRef> {
  return new Map(sections.map((s) => [s.section_id, s]))
}

// УРОВЕНЬ РИСКА. Три слова — те же, что на схеме коллектора: источник —
// `screens/map/risk.ts`, функция `riskLabel`. Здесь они повторены, а не
// импортированы: связывать два экрана ради трёх литералов дороже, чем
// повторить их (решение ведущей MOS-170). Чтобы повтор не разъехался молча —
// НФ-48 требует дословного совпадения, — `rows.selfcheck.ts` сверяет эти
// три строки с `riskLabel` напрямую, и сверка идёт только в проверке,
// в бандл соседний экран не тянется.
export function словоРиска(cls: RiskClass): string {
  if (cls === 'high') return 'высокий риск'
  if (cls === 'normal') return 'низкий риск'
  return 'класса нет'
}

// Цвет плашки слева от строки. Приём взят у экрана заявок, где строка уже
// носит `border-left: 3px solid` цветом приоритета (`screens/orders/index.tsx`):
// одна и та же мысль на двух экранах обязана выглядеть одинаково.
//
// «Класса нет» красим нейтралью рамки, а не серым из шкалы: `--risk-nodata`
// у заказчика занят под «нет связи с каналом» (`dashboard/color-palette.md`
// разд. 5.1), и путать два состояния нельзя. То же решение принято на карте.
export function цветРиска(cls: RiskClass): string {
  if (cls === 'high') return 'var(--risk-critical)'
  if (cls === 'normal') return 'var(--risk-low-border)'
  return 'var(--border-subtle)'
}
