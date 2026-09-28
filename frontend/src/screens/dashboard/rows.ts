// Строка дашборда: имя объекта словами и уровень риска словом и цветом —
// MOS-127, задача 5.16, приёмка М-04. Чистая логика без DOM, проверяется
// node-скриптом (rows.selfcheck.ts), как lag.ts и risk.ts на соседнем экране.

import { имяУчастка } from '../../lib/format.ts'
import type { RiskClass } from './types'

// ИМЯ ОБЪЕКТА. `GET /api/risks` отдаёт только `section_id` — число вида 2477,
// которое диспетчеру не говорит ничего. Тот же объект сервер уже называет
// словами в заявках: `GET /api/orders` отдаёт `object_name` = «Коллектор 645,
// пикет 496». Повторяем ЭТУ строку слово в слово, а не придумываем свою:
// один объект обязан на всех экранах зваться одинаково, иначе диспетчер
// читает два имени как два объекта.
//
// Складываем её на клиенте из `/data/sections.json` — того же справочника,
// который уже читает схема коллектора (`screens/map/index.tsx:50`) и который
// собирался читать журнал. Не поле в ответе API: 3 173 строки × «Коллектор
// 645, пикет 496» это ещё около 70 КБ в ответе, который и так весит 504 421 Б,
// а справочник статический, лежит рядом с бандлом, жмётся до 30,4 КБ и после
// первого экрана берётся из кэша браузера.
export interface SectionRef {
  section_id: number
  smvu_key: string
  collector: number
  collector_name?: string
  picket: number
}

export function имяОбъекта(section: SectionRef | undefined, sectionId: number): string {
  // Справочник не доехал или участка в нём нет — показываем номер, а не пустоту.
  // Пустая ячейка в столбце «Объект» читается как «объекта нет», а он есть.
  if (!section) return `Участок ${sectionId}`
  return имяУчастка(section.smvu_key)
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
//
// Полоска — штрих, поэтому оба класса берут токен ГРАНИЦЫ. Токен заливки
// --risk-critical в тёмной теме — #3a1310 на фоне #131a22, 1,07:1 при норме 3:1
// (MOS-177, НФ-51); граница #d9534a даёт 4,41:1. Меряет code/check_contrast.py.
export function цветРиска(cls: RiskClass): string {
  if (cls === 'high') return 'var(--risk-critical-border)'
  if (cls === 'normal') return 'var(--risk-low-border)'
  return 'var(--border-subtle)'
}

// Вероятность словами диспетчера: «91,5 %», а не «0.9150». Четыре знака после
// точки на экране ничего не различали: у 3 173 участков стенда 28.09.2026
// различных вероятностей всего 16.
export function процент(p: number): string {
  return `${(p * 100).toFixed(1).replace('.', ',')} %`
}

// ОЧАГИ. Где риск сосредоточен — коллекторы по числу участков высокого риска.
// Коллектор — префикс smvu_key, тот же, что в имени участка («Коллектор 1044,
// пикет 0»), чтобы блок и таблица под ним звали его одинаково. `top` — участок
// с наибольшей вероятностью: по нему ссылка ведёт на схему (/map?section=…).
export interface Очаг {
  коллектор: string
  высоких: number
  всего: number
  максимум: number
  top: number
}

export function очаги(
  rows: { section_id: number; probability: number; risk_class: RiskClass }[],
  sections: Map<number, SectionRef>,
): Очаг[] {
  const по = new Map<string, Очаг>()
  for (const r of rows) {
    const s = sections.get(r.section_id)
    if (!s) continue
    const к = s.smvu_key.split(':')[0]
    const о = по.get(к) ?? { коллектор: к, высоких: 0, всего: 0, максимум: -1, top: r.section_id }
    о.всего++
    if (r.risk_class === 'high') о.высоких++
    if (r.probability > о.максимум) {
      о.максимум = r.probability
      о.top = r.section_id
    }
    по.set(к, о)
  }
  return [...по.values()]
    .filter((о) => о.высоких > 0)
    .sort((a, b) => b.высоких - a.высоких || b.максимум - a.максимум)
}
