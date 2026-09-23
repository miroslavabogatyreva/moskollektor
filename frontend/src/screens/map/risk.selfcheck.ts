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

// правило плотности: 455 участков коллектора 914 на ось шириной 920 — превышает
const denseAxis = isDense(455, 920)
assertEqual(denseAxis, true, 'коллектор 914 целиком — густо')

// тот же коллектор, но приближенный до 38 видимых меток — уже помещается
assertEqual(isDense(38, 920), false, '38 видимых меток на 920 — не густо')
assertEqual(isDense(39, 920), true, '39 видимых меток на 920 — уже густо')

if (failed) {
  console.error('risk: самопроверка провалена')
  process.exitCode = 1
} else {
  console.log('risk: самопроверка ок, 11 случаев')
}
