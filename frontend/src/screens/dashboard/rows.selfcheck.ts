// Самопроверка rows.ts — MOS-127, задача 5.16, приёмка М-04 и НФ-48.
// Запуск: node frontend/src/screens/dashboard/rows.selfcheck.ts

declare const process: { exitCode?: number }

import { имяОбъекта, словоРиска, указатель, цветРиска } from './rows.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  if (got !== want) {
    console.error(
      `FAIL: ${label} — получено ${JSON.stringify(got)}, ожидалось ${JSON.stringify(want)}`,
    )
    failed = true
  }
}

// ТРИ СЛОВА, И ОНИ НЕ НАШИ. НФ-48 требует, чтобы на дашборде и на схеме
// коллектора состояние звалось дословно одинаково. Источник — `riskLabel`
// в `screens/map/risk.ts`, здесь они повторены константами.
//
// Сверять их прямым импортом с соседнего экрана я пробовал и снял: `risk.ts`
// ещё не закоммичен (MOS-122 на доработке у автора), и коммит с таким импортом
// уронил бы `npm run typecheck` на чистой выкачке. Ведущая MOS-170 решила
// не связывать экраны ради трёх литералов, дословность сверяет оркестратор
// глазами при приёмке. Значит здесь остаются сами слова, записанные буквально:
// разъедутся — увидит человек, а не проверка, и это принятая цена.
assertEqual(словоРиска('high'), 'высокий риск', 'high — «высокий риск»')
assertEqual(словоРиска('normal'), 'низкий риск', 'normal — «низкий риск»')
assertEqual(словоРиска(null), 'класса нет', 'null — «класса нет»')

// Цвет: high красный из шкалы, normal зелёная рамка, «класса нет» — нейтраль
// рамки, а НЕ --risk-nodata: тот занят под «нет связи с каналом».
assertEqual(цветРиска('high'), 'var(--risk-critical)', 'high — критический')
assertEqual(цветРиска('normal'), 'var(--risk-low-border)', 'normal — низкий')
assertEqual(цветРиска(null), 'var(--border-subtle)', 'нет класса — нейтраль')
assertEqual(цветРиска(null) === 'var(--risk-nodata)', false, 'нет класса ≠ нет связи')

// Real sections from the checked-in customer registry export.
const справочник = указатель([
  {
    section_id: 401,
    smvu_key: '15:0',
    collector: 6,
    collector_ids: [6],
    collector_name: 'объект Бета',
    mapping_status: 'resolved',
    picket: 0,
    kinds: ['guardObject'],
  },
  {
    section_id: 7,
    smvu_key: '1044:1025',
    collector: 7,
    collector_ids: [7],
    collector_name: 'объект Гамма',
    mapping_status: 'resolved',
    picket: 1025,
    kinds: ['controlHouse', 'guardObject'],
  },
  {
    section_id: 2477,
    smvu_key: '889:1',
    collector: 12,
    collector_ids: [12],
    collector_name: 'объект Зита',
    mapping_status: 'resolved',
    picket: 1,
    kinds: ['guardObject'],
  },
  {
    section_id: 1490,
    smvu_key: '798:0',
    collector: null,
    collector_ids: [6, 12],
    collector_name: null,
    mapping_status: 'ambiguous',
    picket: 0,
    kinds: ['controlHouse', 'guardObject'],
  },
])
assertEqual(имяОбъекта(справочник.get(401), 401), 'объект Бета, пикет 0', 'реальное имя участка')
assertEqual(имяОбъекта(справочник.get(7), 7), 'объект Гамма, пикет 1025', 'реальное имя участка')
assertEqual(имяОбъекта(справочник.get(2477), 2477), 'объект Зита, пикет 1', 'реальное имя участка')
assertEqual(
  имяОбъекта(справочник.get(1490), 1490),
  'Участок 798:0 · привязка к коллектору не подтверждена',
  'реальное имя участка',
)
// Справочник не доехал (деплой не атомарен, sections.json едет отдельным
// файлом) — показываем номер, а не пустую ячейку.
assertEqual(имяОбъекта(undefined, 2477), 'Участок 2477', 'без справочника — номер участка')
assertEqual(имяОбъекта(справочник.get(99999), 99999), 'Участок 99999', 'участка нет в справочнике')

if (failed) {
  console.error('rows.selfcheck: есть расхождения')
  process.exitCode = 1
} else {
  console.log('rows.selfcheck ok')
}
