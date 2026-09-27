import { useEffect, useState } from 'preact/hooks'

// Один опрос на приложение — план 5.13 (MOS-123), приёмка НФ-89: заказчик
// разрешил обновлять экран не реже раза в минуту («максимальный интервал -
// 1 минута», ответ 22). Таймер один на вкладку, а не по одному на экран:
// экраны кладут `tick` в зависимости своего useEffect и перезапрашивают свои
// данные сами, шапка показывает `at`. Полоса уведомлений от опроса не зависит,
// она слушает поток GET /api/alerts/stream.
export const POLL_MS = 60_000

let tick = 0
let at = new Date()
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined

export function usePoll(): { tick: number; at: Date } {
  const [, rerender] = useState(0)
  useEffect(() => {
    timer ??= setInterval(() => {
      tick++
      at = new Date()
      listeners.forEach((l) => l())
    }, POLL_MS)
    const l = () => rerender((n) => n + 1)
    listeners.add(l)
    return () => listeners.delete(l)
  }, [])
  return { tick, at }
}
