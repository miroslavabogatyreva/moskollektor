import { useEffect, useState } from 'preact/hooks'

// Один опрос на приложение — план 5.13 (MOS-123), приёмка НФ-89: заказчик
// разрешил обновлять экран не реже раза в минуту («максимальный интервал -
// 1 минута», ответ 22). Таймер один на вкладку, а не по одному на экран:
// экран кладёт `tick` из usePoll() в зависимости своего useEffect, сам
// перезапрашивает свои данные и после УДАЧНОГО ответа зовёт свежо(). Шапка
// показывает время последнего удачного ответа текущего экрана (useLastUpdate),
// а на экране, который не опрашивается, не показывает ничего: время тика
// «обновлено» писало бы и на экране без опроса, и после ответа 500.
// Опрашиваются: дашборд, схема (/map, риски на оси), журнал прогнозов,
// вкладка «Заявки» на /orders. Полоса уведомлений — не отсюда, у неё поток
// GET /api/alerts/stream.
export const POLL_MS = 60_000

let tick = 0
let at: Date | null = null
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined
let экранов = 0

const оповестить = () => listeners.forEach((l) => l())

function useПодписка() {
  const [, rerender] = useState(0)
  useEffect(() => {
    const l = () => rerender((n) => n + 1)
    listeners.add(l)
    return () => listeners.delete(l)
  }, [])
}

export function свежо() {
  at = new Date()
  оповестить()
}

export function usePoll(): { tick: number } {
  useПодписка()
  useEffect(() => {
    timer ??= setInterval(() => {
      tick++
      оповестить()
    }, POLL_MS)
    // Ушли с опрашиваемого экрана на экран без опроса — время в шапке больше
    // не про то, что видно. Переход между опрашиваемыми экранами время не
    // стирает: иначе строка гасла до ответа нового экрана и шапка мигала
    // при каждом переходе по меню (Слава, 29.09.2026). Проверяем после
    // отрисовки: новый экран монтируется уже после размонтирования старого.
    экранов++
    return () => {
      экранов--
      setTimeout(() => {
        if (экранов > 0) return
        at = null
        оповестить()
      })
    }
  }, [])
  return { tick }
}

export function useLastUpdate(): Date | null {
  useПодписка()
  return at
}
