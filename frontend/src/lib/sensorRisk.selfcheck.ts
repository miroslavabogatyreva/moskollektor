// Самопроверка sensorRisk.ts — эпик MOS-248, SL.5 и SL.6.
// Запуск: node frontend/src/lib/sensorRisk.selfcheck.ts

declare const process: { exitCode?: number }

import {
  БЕЗ_ЛИНИИ,
  главнаяПричина,
  линииДатчиков,
  сПараметром,
  синтетикаВключена,
  уровеньИзАдреса,
  процент,
  доляПричины,
  страница,
  sensorRiskUrl,
} from './sensorRisk.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    console.error(
      `FAIL: ${label} — получено ${JSON.stringify(got)}, ожидалось ${JSON.stringify(want)}`,
    )
    failed = true
  }
}

// Балл в процентах с одним знаком (MOS-263): 0,0073 → «0,7 %».
assertEqual(процент(0.0073), '0,7\u00a0%', '0,0073 → 0,7 %')
assertEqual(процент(0.19), '19,0\u00a0%', '0,19 → 19,0 %')
assertEqual(процент(0.0002), '<\u00a00,1\u00a0%', 'меньше 0,05 % — не ноль')
assertEqual(процент(0), '0\u00a0%', 'ноль — ноль')
assertEqual(доляПричины(0.002, 0.008), 0.25, 'четверть балла')
assertEqual(доляПричины(0.01, 0), 0, 'балл 0 — полоски нет')

// Переключатель: включён всегда, кроме явного ?synthetic=0.
assertEqual(синтетикаВключена(undefined), true, 'нет параметра → синтетика включена')
assertEqual(синтетикаВключена('1'), true, '?synthetic=1 → включена')
assertEqual(синтетикаВключена('0'), false, '?synthetic=0 → выключена')

// Фильтр уровня: три уровня как есть, остальное — «все».
assertEqual(уровеньИзАдреса('high'), 'high', '?level=high')
assertEqual(уровеньИзАдреса('watch'), 'watch', '?level=watch')
assertEqual(уровеньИзАдреса(undefined), '', 'нет параметра → все')
assertEqual(уровеньИзАдреса('HIGH'), '', 'чужое значение → все')

assertEqual(
  sensorRiskUrl({ synthetic: false, collector: 3828, limit: 5000 }),
  '/api/sensor-risk?synthetic=0&collector=3828&limit=5000',
  'адрес метода для схемы',
)
assertEqual(
  sensorRiskUrl({ synthetic: true, limit: 50, offset: 0 }),
  '/api/sensor-risk?synthetic=1&limit=50',
  'offset 0 в адрес не пишем',
)

// Параметр адреса меняется один, остальные остаются.
assertEqual(
  сПараметром('/map', '?channel=267052', 'synthetic', '0'),
  '/map?channel=267052&synthetic=0',
  'выключили синтетику — channel остался',
)
assertEqual(
  сПараметром('/map', '?channel=267052&synthetic=0', 'synthetic', null),
  '/map?channel=267052',
  'включили обратно — параметр убран',
)
assertEqual(сПараметром('/dashboard', '?synthetic=0', 'synthetic', null), '/dashboard', 'пусто')

// Главная причина — самая весомая не плановая.
const real = { text: 'отказов 9', weight: 0.3, kind: 'real' as const }
const synth = { text: 'срок службы', weight: 0.09, kind: 'synthetic' as const }
const plan = { text: 'ППР 04.06', weight: 0, kind: 'plan' as const }
assertEqual(главнаяПричина([synth, real, plan])?.text, 'отказов 9', 'весомая побеждает')
assertEqual(главнаяПричина([plan])?.text, 'ППР 04.06', 'только плановая — её и показываем')
assertEqual(главнаяПричина([]), null, 'причин нет')

// Разделитель разрядов у ru-RU — неразрывный пробел U+00A0.
assertEqual(страница(0, 50, 11485), '1–50 из 11\u00a0485', 'первая страница')
assertEqual(
  страница(11450, 35, 11485),
  '11\u00a0451–11\u00a0485 из 11\u00a0485',
  'последняя страница',
)
assertEqual(страница(0, 0, 0), '0 из 0', 'пустая выборка')

// Линии: по префиксу ключа участка; без участка — в «—», и она последняя.
const ключи = new Map([
  [1, '915:3'],
  [2, '914:10'],
])
const линии = линииДатчиков(
  [{ section_id: 1 }, { section_id: 2 }, { section_id: null }, { section_id: 99 }],
  ключи,
)
assertEqual(
  линии.map(([p, v]) => [p, v.length]),
  [
    ['914', 1],
    ['915', 1],
    [БЕЗ_ЛИНИИ, 2],
  ],
  'линии по префиксу, «—» в конце',
)

if (failed) process.exitCode = 1
else console.log('sensorRisk.ts: всё сходится')
