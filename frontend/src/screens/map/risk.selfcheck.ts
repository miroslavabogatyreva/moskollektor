// Самопроверка risk.ts — MOS-106, задача 5.22, приёмка М-05.
// Запуск: node frontend/src/screens/map/risk.selfcheck.ts

declare const process: { exitCode?: number }

import { isDense, riskColors, riskLabel, riskShape } from './risk.ts'

let failed = false
const assertEqual = (got: unknown, want: unknown, label: string) => {
  if (got !== want) {
    console.error(
      `FAIL: ${label} — получено ${JSON.stringify(got)}, ожидалось ${JSON.stringify(want)}`,
    )
    failed = true
  }
}

// high красится критическим, normal — низким; сервер сегодня не отдаёт ничего третьего
assertEqual(riskColors('high').fill, 'var(--risk-critical)', 'high → критический')
assertEqual(riskColors('normal').fill, 'var(--risk-low)', 'normal → низкий')

// нет класса (поле ещё не пришло или участок не найден) — нейтраль рамки значка,
// не серый из шкалы: тот у заказчика занят под "нет данных" по каналу (MOS-170)
assertEqual(riskColors(undefined).fill, 'var(--bg-table)', 'нет класса → нейтраль')
assertEqual(riskColors(null).border, 'var(--border-subtle)', 'null → нейтраль')

// подписи легенды (MOS-170) — одно название на /map и на дашборде
assertEqual(riskLabel('high'), 'высокий риск', 'high → подпись')
assertEqual(riskLabel('normal'), 'низкий риск', 'normal → подпись')
assertEqual(riskLabel(undefined), 'класса нет', 'нет класса → подпись')

// форма (MOS-173): у трёх состояний три разные формы — цвет не работает один
const формы = new Set([riskShape('high'), riskShape('normal'), riskShape(null)])
assertEqual(формы.size, 3, 'три состояния — три формы')
assertEqual(riskShape(undefined), riskShape(null), 'undefined и null — одно состояние')

// правило плотности (MOS-215): расстояние между соседями, а не число меток.
// Координаты — как в AxisLine на полной оси: пикет / максимум × 920.
const оси = (пикеты: number[]) => {
  const макс = Math.max(...пикеты)
  return пикеты.map((п) => (п / макс) * 920)
}
// Линия 798 объекта Зита со стенда 27.09.2026: 21 метка, по числу помещалась
// (21 × 24 < 920), но ПК55/56/57 стоят через 13 единиц — рамки 20×20 налезали.
const линия798 = [0, 1, 6, 20, 22, 32, 35, 50, 55, 56, 57, 60, 61, 62, 64, 65, 66, 67, 68, 69, 71]
assertEqual(isDense(оси(линия798)), true, 'линия 798: 21 метка, соседи через 13 — густо')
// Та же линия у объекта Бета — 4 метки через 77 и больше: номера остаются.
assertEqual(isDense(оси([3, 11, 81, 96])), false, 'линия 798 Беты: 4 метки — не густо')
// Линия 854: всего 7 меток, но ПК175 и ПК177 через 4,1 единицы.
assertEqual(isDense(оси([175, 177, 192, 254, 274, 294, 451])), true, 'линия 854: 7 меток, пара через 4,1 — густо')
// 38 меток ровно через 24 — рамки не касаются; сдвинь одну на 1 — уже густо.
const ровно = Array.from({ length: 38 }, (_, i) => i * 24)
assertEqual(isDense(ровно), false, '38 меток через 24 — не густо')
assertEqual(isDense([...ровно.slice(0, 37), 36 * 24 + 23]), true, 'пара через 23 — густо')
// Порядок входа не важен: AxisLine отдаёт метки в порядке данных, не по пикету.
assertEqual(isDense([500, 0, 510]), true, 'неотсортированный вход — пара через 10 найдена')
assertEqual(isDense([100]), false, 'одна метка — не густо')
assertEqual(isDense([]), false, 'пусто — не густо')

if (failed) {
  console.error('risk: самопроверка провалена')
  process.exitCode = 1
} else {
  console.log('risk: самопроверка ок, 17 случаев')
}
