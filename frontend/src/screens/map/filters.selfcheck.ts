// Самопроверка filters.ts — MOS-122, задача 5.12, приёмка Ф-93.
// Запуск: node frontend/src/screens/map/filters.selfcheck.ts

declare const process: { exitCode?: number }

import { matchesFilters, type MapFilterState } from './filters.ts'
import type { RiskClass } from './risk.ts'
import type { Section } from './types.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  if (got !== want) {
    console.error(
      `FAIL: ${label} — получено ${JSON.stringify(got)}, ожидалось ${JSON.stringify(want)}`,
    )
    failed = true
  }
}

const guard: Section = {
  section_id: 1,
  smvu_key: '1:1',
  collector: 1,
  collector_name: 'тестовый коллектор',
  picket: 1,
  kinds: ['guardObject'],
}
const control: Section = {
  section_id: 2,
  smvu_key: '1:2',
  collector: 1,
  collector_name: 'тестовый коллектор',
  picket: 2,
  kinds: ['controlHouse'],
}
const both: Section = {
  section_id: 3,
  smvu_key: '1:3',
  collector: 1,
  collector_name: 'тестовый коллектор',
  picket: 3,
  kinds: ['controlHouse', 'guardObject'],
}

const all: MapFilterState = { risk: 'all', kind: 'all' }
assertEqual(matchesFilters(guard, 'high', all), true, 'без фильтров проходит всё')
assertEqual(matchesFilters(guard, undefined, all), true, 'без фильтров проходит и без класса риска')

assertEqual(
  matchesFilters(guard, 'high', { risk: 'high', kind: 'all' }),
  true,
  'риск high проходит фильтр high',
)
assertEqual(
  matchesFilters(guard, 'normal', { risk: 'high', kind: 'all' }),
  false,
  'риск normal режется фильтром high',
)
assertEqual(
  matchesFilters(guard, undefined, { risk: 'high', kind: 'all' }),
  false,
  'нет класса — режется фильтром high',
)

assertEqual(
  matchesFilters(guard, 'high', { risk: 'all', kind: 'guardObject' }),
  true,
  'guardObject проходит свой фильтр',
)
assertEqual(
  matchesFilters(control, 'high', { risk: 'all', kind: 'guardObject' }),
  false,
  'controlHouse режется фильтром guardObject',
)
assertEqual(
  matchesFilters(both, 'high', { risk: 'all', kind: 'guardObject' }),
  true,
  'участок с двумя видами проходит по любому из них',
)
assertEqual(
  matchesFilters(both, 'high', { risk: 'all', kind: 'controlHouse' }),
  true,
  'тот же участок проходит и по второму виду',
)

assertEqual(
  matchesFilters(both, 'high', { risk: 'high', kind: 'controlHouse' }),
  true,
  'фильтры складываются — оба условия верны',
)
assertEqual(
  matchesFilters(both, 'normal', { risk: 'high', kind: 'controlHouse' }),
  false,
  'фильтры складываются — риск не подходит, весь отбор режется',
)

// Участок БЕЗ поля kinds — не выдумка, а сегодняшнее состояние стенда (проверяющая
// сессия, 22.09.2026, MOS-122). `sections.json` едет на сервер отдельным файлом
// данных, бандл — отдельно, и между выкладками они расходятся: новый бандл читает
// старый файл, где поля ещё нет. Живой замер через браузер: стенд отдаёт участки
// с четырьмя ключами (section_id, smvu_key, collector, picket), и при выборе типа
// объекта экран карты валился шесть раз подряд с
// `TypeError: Cannot read properties of undefined (reading 'includes')`,
// а счётчик при этом показывал «303 из 303» — то есть фильтр молча не резал ничего.
// Лечится в двух местах: filters.ts одним `?? []`, и types.ts — kinds стал
// `kinds?: ObjectKind[]`, чтобы компилятор сам находил будущих читателей без
// защиты, а не только этого. Поэтому приведение типа здесь больше не нужно:
// участок без kinds — легитимный Section, а не подделка под него.
const без_вида: Section = { section_id: 401, smvu_key: '15:0', collector: 15, picket: 0 }
// Через обёртку, а не напрямую: сегодня matchesFilters на таком участке БРОСАЕТ,
// и голый вызов уронил бы весь файл на этой строке — остальные случаи просто
// не выполнились бы, а прогон напечатал бы стек вместо списка того, что не сошлось.
const безопасно = (s: Section, r: RiskClass, f: MapFilterState): boolean | string => {
  try {
    return matchesFilters(s, r, f)
  } catch (e) {
    return `ИСКЛЮЧЕНИЕ: ${(e as Error).message}`
  }
}
assertEqual(
  безопасно(без_вида, 'normal', { risk: 'all', kind: 'all' }),
  true,
  'участок без поля kinds проходит, когда по виду не фильтруют',
)
assertEqual(
  безопасно(без_вида, 'normal', { risk: 'all', kind: 'controlHouse' }),
  false,
  'участок без поля kinds не проходит отбор по виду и не роняет экран',
)

if (failed) {
  console.error('filters: самопроверка провалена')
  process.exitCode = 1
} else {
  console.log('filters: самопроверка ок, 12 случаев')
}
