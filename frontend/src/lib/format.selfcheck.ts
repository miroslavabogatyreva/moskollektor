// Самопроверка дат по Москве (MOS-121, строки приёмки М-12, Ф-33). Прогонять
// в двух поясах — пояс машины на результат влиять не должен:
//
//     TZ=UTC              node frontend/src/lib/format.selfcheck.ts
//     TZ=Asia/Vladivostok node frontend/src/lib/format.selfcheck.ts

declare const process: { exitCode?: number; env: Record<string, string | undefined> }

import { formatDate, formatDateTime, formatTime, isoDateMoscow } from './format.ts'

let failed = false
function eq(got: string, want: string, what: string) {
  if (got !== want) {
    console.error(`FAIL ${what}: ${got} != ${want}`)
    failed = true
  }
}

// Пример из задачи 5.11: 20:59:59 UTC — это 23:59:59 по Москве, а во Владивостоке
// (UTC+10) уже 06:59 следующих суток; экран обязан показать московские.
const край = '2026-06-29T20:59:59+00:00'
eq(formatDateTime(край), '29.06.2026 23:59', 'дата и время')
eq(formatDateTime(край, true), '29.06.2026 23:59:59', 'с секундами')
eq(formatDate(край), '29.06.2026', 'дата')
eq(formatTime(край), '23:59:59', 'время')
eq(isoDateMoscow(край), '2026-06-29', 'дата для поля ввода')
// Минута позже — московская полночь: сутки сменились, и часы 00, а не 24.
eq(formatDateTime('2026-06-29T21:00:00Z'), '30.06.2026 00:00', 'полночь')
eq(isoDateMoscow(new Date('2026-06-29T21:00:00Z')), '2026-06-30', 'полночь, поле ввода')
// Наивная строка сервера без пояса читается браузером как местная — это не наш
// случай: API отдаёт моменты с поясом, как в примере выше.

if (failed) process.exitCode = 1
else console.log(`format selfcheck ok (TZ=${process.env.TZ ?? 'системный'}): даты по Москве`)
