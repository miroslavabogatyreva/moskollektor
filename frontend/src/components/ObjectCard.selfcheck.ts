// Самопроверка арифметики карточки объекта (MOS-131, MOS-52), строка приёмки
// Ф-91. Отдельный файл — ObjectCard.tsx это JSX, а Node вырезает типы из .ts,
// но не умеет JSX; бандл его не подхватывает, он сюда не импортируется.
//
// Запуск: node frontend/src/components/ObjectCard.selfcheck.ts

declare const process: { exitCode?: number }

import {
  axisTicks,
  defaultWindow,
  fmtValue,
  groupChannelsBySystem,
  groupRepeatedForecasts,
  NO_SYSTEM_KIND,
  shortDate,
  type RecentForecast,
} from './ObjectCard.logic.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  const g = JSON.stringify(got)
  const w = JSON.stringify(want)
  if (g !== w) {
    console.error(`FAIL: ${label} — получено ${g}, ожидалось ${w}`)
    failed = true
  }
}

// окно по умолчанию — 7 суток, оканчивающихся последней записью участка
assertEqual(
  defaultWindow('2026-04-22T08:38:29+00:00'),
  ['2026-04-16', '2026-04-22'],
  'окно по last_reading_at',
)
assertEqual(defaultWindow(null) !== undefined, true, 'окно без last_reading_at не падает')

// участок 401 (нашла ab, 22.09.2026): 21:55 UTC — это уже следующие сутки
// по Москве, дата окончания окна обязана сдвинуться на день вперёд
assertEqual(
  defaultWindow('2026-06-29T21:55:45+00:00'),
  ['2026-06-24', '2026-06-30'],
  'окно считает сутки по Москве, не по UTC',
)

// "2026-04-16" → "16.04", без года — подпись к фразе про пустое окно
assertEqual(shortDate('2026-04-16'), '16.04', 'короткая дата')

// подпись минимума/максимума числового ряда — без выдуманной единицы
assertEqual(fmtValue(26), '26', 'целое без дробной части')
assertEqual(fmtValue(19.5), '19,50', 'дробное через запятую')

// ось времени: 4 деления, включая начало и конец, равный шаг
const ticks = axisTicks(0, 3000, 4)
assertEqual(ticks, [0, 1000, 2000, 3000], 'деления оси равномерны')
assertEqual(axisTicks(100, 100, 4), [100, 100, 100, 100], 'нулевая длина окна не делит на ноль')

// схлопывание повторов — только соседних, с сохранением первого (самого свежего)
const mk = (over: Partial<RecentForecast>): RecentForecast => ({
  forecast_id: 1,
  direction: 'sensor_failure',
  probability: 0.9991,
  risk_rank: 1,
  explanation_ru: 'Отказ датчика',
  computed_at: '2026-09-22T09:40:00Z',
  ...over,
})
const grouped = groupRepeatedForecasts([
  mk({ forecast_id: 10, computed_at: '11:14:02' }),
  mk({ forecast_id: 11, computed_at: '11:00:00' }),
  mk({ forecast_id: 12, probability: 0.5643, risk_rank: 2433, computed_at: '10:40:00' }),
  mk({ forecast_id: 13, computed_at: '10:36:00' }),
])
assertEqual(
  grouped.map((g) => [g.forecast.forecast_id, g.repeats]),
  [
    [10, 2],
    [12, 1],
    [13, 1],
  ],
  'повторы схлопнуты, порядок и id самой свежей записи сохранены',
)
assertEqual(groupRepeatedForecasts([]), [], 'пустой список не падает')

// Схлопывать можно только полностью одинаковые записи — проверяем каждое из
// четырёх полей порознь. Разбор 22.09.2026 (проверяющая сессия): случай выше
// меняет у записи 12 сразу probability и risk_rank, поэтому он оставался
// зелёным, если убрать из сравнения любое ОДНО из четырёх полей. Правило было
// не защищено: без сравнения probability две записи с разной вероятностью
// слились бы в одну строку, и диспетчер увидел бы «10×» там, где прогнозы
// расходились в четвёртом знаке.
const ONE_FIELD_APART: [string, Partial<RecentForecast>][] = [
  ['direction', { direction: 'fire' }],
  ['probability', { probability: 0.5643 }],
  ['risk_rank', { risk_rank: 2433 }],
  ['explanation_ru', { explanation_ru: 'Другой текст' }],
]
for (const [field, diff] of ONE_FIELD_APART) {
  assertEqual(
    groupRepeatedForecasts([mk({ forecast_id: 1 }), mk({ forecast_id: 2, ...diff })]).map(
      (g) => g.repeats,
    ),
    [1, 1],
    `различие только в ${field} не схлопывается`,
  )
}

// группировка каналов по system_kind (MOS-172) — участок с одной системой
const mkCh = (system_kind: string | null) => ({ channel_id: 0, system_kind })
assertEqual(
  groupChannelsBySystem([
    mkCh('Газовая охрана'),
    mkCh('Газовая охрана'),
    mkCh('Газовая охрана'),
  ]).map((g) => [g.systemKind, g.channels.length]),
  [['Газовая охрана', 3]],
  'один участок с одной системой — одна группа',
)

// участок с шестью системами — порядок групп как в списке каналов, не алфавитный
const SIX = [
  'Пожарная охрана',
  'Диспетчерский контроль',
  'Охранная подсистема',
  'Температурная подсистема',
  'Газовая охрана',
  'Диагностическая подсистема',
]
assertEqual(
  groupChannelsBySystem(SIX.map(mkCh)).map((g) => g.systemKind),
  SIX,
  'участок с шестью системами — шесть групп по первому появлению',
)

// пустая строка и null — одна и та же группа "Без системы". На карточке сегодня
// таких нет вовсе: все 1 142 канала без вида имеют section_id IS NULL (замер
// 22.09.2026). Случай сторожит будущие данные, а не нынешние.
assertEqual(
  groupChannelsBySystem([mkCh(''), mkCh(null), mkCh('Охранная подсистема')]).map((g) => [
    g.systemKind,
    g.channels.length,
  ]),
  [
    [NO_SYSTEM_KIND, 2],
    ['Охранная подсистема', 1],
  ],
  'пустая строка и null схлопываются в одну группу "Без системы"',
)

// участок на сотню каналов (как 2204) — ни один канал не потерян и не задвоен
const hundred = Array.from({ length: 100 }, (_, i) => mkCh(SIX[i % SIX.length]))
const hundredGrouped = groupChannelsBySystem(hundred)
assertEqual(
  hundredGrouped.reduce((sum, g) => sum + g.channels.length, 0),
  100,
  'сто каналов — группы в сумме не теряют и не дублируют каналы',
)
assertEqual(hundredGrouped.length, SIX.length, 'сто каналов на шесть систем — шесть групп, не сто')

if (failed) {
  console.error('ObjectCard.selfcheck: ЕСТЬ ОШИБКИ')
  process.exitCode = 1
} else {
  console.log('ObjectCard.selfcheck: OK')
}
