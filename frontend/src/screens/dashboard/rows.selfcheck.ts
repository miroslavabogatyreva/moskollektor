// Самопроверка rows.ts — MOS-127, задача 5.16, приёмка М-04 и НФ-48.
// Запуск: node frontend/src/screens/dashboard/rows.selfcheck.ts

declare const process: { exitCode?: number }

import { имяОбъекта, очаги, процент, словоРиска, указатель, цветРиска } from './rows.ts'

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
assertEqual(цветРиска('high'), 'var(--risk-critical-border)', 'high — граница критического')
assertEqual(цветРиска('normal'), 'var(--risk-low-border)', 'normal — низкий')
assertEqual(цветРиска(null), 'var(--border-subtle)', 'нет класса — нейтраль')
assertEqual(цветРиска(null) === 'var(--risk-nodata)', false, 'нет класса ≠ нет связи')

// ИМЯ ОБЪЕКТА слово в слово как у сервера: asset.func_location.name =
// 'Коллектор ' || префикс smvu_key || ', пикет ' || пикет (db/migrations/010_orders.sql:155),
// его отдаёт GET /api/orders полем object_name.
//
// НА НАСТОЯЩЕМ sections.json, а не на подставной таблице (MOS-243). Подставная держала
// collector = префикс тега и не видела, что после MOS-181 в файле collector — номер
// коллектора дерева (16 значений): дашборд звал 2300 «Коллектор 15, пикет 730», а сервер —
// «Коллектор 884, пикет 730». Сверяем все 3 173 участка с правилом сервера, собранным
// здесь заново из smvu_key, — вернёт кто-нибудь имя на collector, проверка покраснеет
// на каждом участке, где коллектор дерева не равен префиксу (сегодня на всех).
import настоящий from '../../../public/data/sections.json' with { type: 'json' }

const участки = настоящий as {
  section_id: number
  smvu_key: string
  collector: number
  picket: number
}[]
const справочник = указатель(участки)
assertEqual(участки.length > 3000, true, `sections.json прочитан: ${участки.length} участков`)
let расхождений = 0
for (const у of участки) {
  const [префикс, пикет] = у.smvu_key.split(':')
  if (имяОбъекта(у, у.section_id) !== `Коллектор ${префикс}, пикет ${пикет}`) расхождений++
}
assertEqual(расхождений, 0, 'имя дашборда = правило сервера на всех участках sections.json')

// Три участка с живыми именами из заявок и тикетов.
assertEqual(
  имяОбъекта(справочник.get(2477), 2477),
  'Коллектор 889, пикет 1',
  'US-14: участок из тикета',
)
assertEqual(
  имяОбъекта(справочник.get(2300), 2300),
  'Коллектор 884, пикет 730',
  'MOS-243: 2300 как в заявке',
)
// Пикет 0 — настоящий пикет, а не «нет пикета»: проверка ловит подстановку
// вида `picket || '—'`, на нуле она бы соврала. 401 — ещё и коллектор дерева 6 ≠ префикс 15.
assertEqual(
  имяОбъекта(справочник.get(401), 401),
  'Коллектор 15, пикет 0',
  'нулевой пикет, префикс, а не дерево',
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

// Проценты и очаги (дашборд диспетчера, 28.09.2026).
assertEqual(процент(0.9149649739265442), '91,5 %', 'вероятность — процент с запятой')
assertEqual(процент(0.05), '5,0 %', 'малая вероятность — один знак')
{
  const с = указатель([
    { section_id: 1, smvu_key: '1044:0', collector: 5, picket: 0 },
    { section_id: 2, smvu_key: '1044:7', collector: 5, picket: 7 },
    { section_id: 3, smvu_key: '889:1', collector: 2, picket: 1 },
    { section_id: 4, smvu_key: '889:2', collector: 2, picket: 2 },
  ])
  const о = очаги(
    [
      { section_id: 1, probability: 0.5, risk_class: 'high' },
      { section_id: 2, probability: 0.9, risk_class: 'high' },
      { section_id: 3, probability: 0.95, risk_class: 'high' },
      { section_id: 4, probability: 0.1, risk_class: 'normal' },
      { section_id: 99, probability: 0.99, risk_class: 'high' }, // нет в справочнике — пропуск
    ],
    с,
  )
  assertEqual(о.length, 2, 'два коллектора с высоким риском')
  assertEqual(
    о[0].коллектор,
    '1044',
    'первый — где больше участков высокого риска, а не где выше максимум',
  )
  assertEqual(о[0].top, 2, 'ссылка ведёт на самый рискованный участок коллектора')
  assertEqual(о[1].всего, 2, 'всего считает и участки низкого риска')
  assertEqual(о[1].высоких, 1, 'высоких — только high')
}
if (!failed) console.log('rows.selfcheck: проценты и очаги ok')
