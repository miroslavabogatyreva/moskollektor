// Самопроверка plural.ts — MOS-262.
// Запуск: node frontend/src/lib/plural.selfcheck.ts

declare const process: { exitCode?: number }

import { датчиков, изУчастков, коллекторах, линий } from './plural.ts'

let failed = false
const eq = (got: string, want: string) => {
  if (got !== want) {
    console.error(`FAIL: ${got} != ${want}`)
    failed = true
  }
}

const ждём: [number, string][] = [
  [1, '1 датчик'],
  [2, '2 датчика'],
  [5, '5 датчиков'],
  [11, '11 датчиков'],
  [21, '21 датчик'],
  [104, '104 датчика'],
]
for (const [n, want] of ждём) eq(датчиков(n), want)
eq(датчиков(0), '0 датчиков')
eq(датчиков(12), '12 датчиков')
eq(коллекторах(1), '1 коллекторе')
eq(коллекторах(3), '3 коллекторах')
eq(линий(1), '1 линия')
eq(линий(5), '5 линий')
eq(изУчастков(21), '21 участка')
eq(изУчастков(11), '11 участков')

if (failed) process.exitCode = 1
else console.log('plural selfcheck ok: 1, 2, 5, 11, 21, 104')
