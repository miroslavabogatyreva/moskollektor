import type { ComponentChildren } from 'preact'
import { PRIORITY_LABEL, STATUS_LABEL } from '../screens/orders/types'

// Бейдж — цветная плашка со словом (классы .badge-* в styles/index.css). Слово
// остаётся всегда: цвет помогает глазу, но статус читается и без него.
export type Tone = 'info' | 'success' | 'warning' | 'danger' | 'neutral'

export function Badge({ tone, children }: { tone: Tone; children: ComponentChildren }) {
  return <span class={`badge badge-${tone}`}>{children}</span>
}

const STATUS_TONE: Record<string, Tone> = {
  OPEN: 'info',
  IN_PROCESS: 'warning',
  COMPLETED: 'success',
  CANCELLED: 'neutral',
}

export function OrderStatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{STATUS_LABEL[status] ?? status}</Badge>
}

const PRIORITY_TONE: Record<string, Tone> = { '1': 'danger', '2': 'warning', '3': 'info' }

export function PriorityBadge({ code, name }: { code: string; name?: string }) {
  return (
    <Badge tone={PRIORITY_TONE[code] ?? 'neutral'}>{PRIORITY_LABEL[code] ?? name ?? code}</Badge>
  )
}

// Статус из системы учёта приходит строкой по-русски («выполнена», «в работе»…).
export function externalTone(s: string): Tone {
  const t = s.toLowerCase()
  if (/выполн|закрыт/.test(t)) return 'success'
  if (/работ|назнач|выезд/.test(t)) return 'warning'
  if (/отмен|отклон/.test(t)) return 'neutral'
  return 'info'
}
