// Самопроверка шкалы метана (MOS-169, приёмка Ф-30). Бандл её не подхватывает.
//
// Запуск: node frontend/src/components/GasScale.selfcheck.ts

declare const process: { exitCode?: number }

import { меткаМетана, позиция, последнееЧисло, УСТАВКИ_МЕТАНА } from './GasScale.logic.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  const g = JSON.stringify(got)
  const w = JSON.stringify(want)
  if (g !== w) {
    console.error(`FAIL: ${label} — получено ${g}, ожидалось ${w}`)
    failed = true
  }
}

// отметки уставок на шкале 0–2 % об.
assertEqual(
  УСТАВКИ_МЕТАНА.map((v) => позиция(v)),
  [37.5, 75],
  'уставки 0,75 и 1,5 на шкале',
)

// 1. ниже первой уставки
assertEqual(меткаМетана(0.3), { zone: 'below', pct: 15, outOfScale: null }, 'ниже 0,75')

// 2. между ступенями — проверка Ф-30: метка правее 0,75 и левее 1,5
const м = меткаМетана(0.8)
assertEqual(м.zone, 'between', '0,8 — между ступенями')
assertEqual(м.pct! > позиция(0.75) && м.pct! < позиция(1.5), true, '0,8 — метка между отметками')
assertEqual(меткаМетана(0.75).zone, 'between', 'ровно 0,75 — уже первая ступень')

// 3. выше второй уставки
assertEqual(меткаМетана(1.91), { zone: 'above', pct: 95.5, outOfScale: null }, 'выше 1,5')
assertEqual(меткаМетана(1.5).zone, 'above', 'ровно 1,5 — уже вторая ступень')

// 4. вне диапазона шкалы — прижато к краю и помечено
assertEqual(меткаМетана(40), { zone: 'above', pct: 100, outOfScale: 'right' }, 'выше шкалы')
assertEqual(меткаМетана(-1), { zone: 'below', pct: 0, outOfScale: 'left' }, 'ниже шкалы')

// 5. нет значения
assertEqual(меткаМетана(null), { zone: 'none', pct: null, outOfScale: null }, 'нет значения')
assertEqual(меткаМетана(NaN).zone, 'none', 'NaN — тоже нет значения')

// последнее число за окно — пропуская записи состояний
const з = (read_time: string, value_num: number | null) => ({ read_time, value_num })
assertEqual(
  последнееЧисло([
    з('2026-06-01T10:00:00Z', 0.2),
    з('2026-06-01T12:00:00Z', 0.8),
    з('2026-06-01T13:00:00Z', null),
  ]),
  з('2026-06-01T12:00:00Z', 0.8),
  'последнее число, а не последняя запись',
)
assertEqual(последнееЧисло([з('2026-06-01T13:00:00Z', null)]), null, 'одни состояния — числа нет')

if (failed) {
  console.error('GasScale.selfcheck: ЕСТЬ ОШИБКИ')
  process.exitCode = 1
} else {
  console.log('GasScale.selfcheck: OK')
}
