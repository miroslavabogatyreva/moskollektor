// Шкала загазованности в карточке объекта (MOS-169, план 5.26, приёмка Ф-30).
// Отдельный файл без JSX — тем же приёмом, что ObjectCard.logic.ts, — чтобы
// GasScale.selfcheck.ts импортировал его напрямую через node.
//
// Газовый датчик пишет процент объёма метана — так ответил заказчик 23.09.2026
// (docs/for-ml-team.md разд. 18). Уставки — из Ф-30: 0,75 % об. (15 % НКПР, GB 30871-2022)
// и 1,5 % об. Кислородную шкалу (19,5 и 23,5 % об.) не рисуем: в выгрузке ни одного
// кислородного канала — 0 из 12 627 в docs/proof/2026-09-28-sensor-model/data/channels.csv.
export const УСТАВКИ_МЕТАНА = [0.75, 1.5] as const

// Шкала 0–2 % об.: за 25–30.06.2026 газовые каналы писали от 0 до 1,91 % об. (план 5.26),
// обе уставки ложатся на неё с запасом справа. Отрицательное число заказчик называет
// неисправностью, выше 2 % об. — редкость (один канал держит 34–46 %), такие значения
// прижимаем к краю и подписываем «за шкалой», а не растягиваем шкалу под них.
export const ШКАЛА_МЕТАНА: [number, number] = [0, 2]

// below — ниже первой уставки, between — между ступенями, above — от второй и выше,
// none — числа нет (канал молчит или пишет состояния).
export type Зона = 'below' | 'between' | 'above' | 'none'

export interface Метка {
  zone: Зона
  pct: number | null // положение метки от левого края шкалы, 0–100
  outOfScale: 'left' | 'right' | null
}

export function меткаМетана(
  value: number | null,
  [lo, hi]: [number, number] = ШКАЛА_МЕТАНА,
  [first, second]: readonly [number, number] = УСТАВКИ_МЕТАНА,
): Метка {
  if (value == null || !Number.isFinite(value)) return { zone: 'none', pct: null, outOfScale: null }
  const zone: Зона = value < first ? 'below' : value < second ? 'between' : 'above'
  const outOfScale = value < lo ? 'left' : value > hi ? 'right' : null
  const clamped = Math.min(hi, Math.max(lo, value))
  return { zone, pct: ((clamped - lo) / (hi - lo)) * 100, outOfScale }
}

// Положение отметки уставки на той же шкале, в процентах.
export function позиция(v: number, [lo, hi]: [number, number] = ШКАЛА_МЕТАНА): number {
  return ((v - lo) / (hi - lo)) * 100
}

// Последнее числовое показание канала за окно: записи состояний («Норма»,
// «Неисправен») числа не несут и метку не двигают.
export function последнееЧисло<R extends { read_time: string; value_num: number | null }>(
  readings: R[],
): R | null {
  let last: R | null = null
  for (const r of readings)
    if (r.value_num != null && (!last || r.read_time > last.read_time)) last = r
  return last
}
