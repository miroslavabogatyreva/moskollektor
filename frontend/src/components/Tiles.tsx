import { formatDateTime } from '../lib/format'
import { процент } from '../screens/dashboard/rows'
import type { Unacked } from '../screens/dashboard/types'

/* Плитка, панель и журнал «Ждут квитирования» — общие для дашборда и главной
   (схема пикетов, MOS-265): одна вёрстка и одни подписи на обоих экранах. */

export function Panel({ title, children }: { title: string; children: preact.ComponentChildren }) {
  return (
    <section class="card flex flex-col gap-3">
      <h2 class="card-title">{title}</h2>
      {children}
    </section>
  )
}

// Неквитированные уведомления, свежие сверху; строка ведёт в карточку участка.
export function UnackedPanel({ unacked }: { unacked: Unacked | null }) {
  return (
    <Panel title="Ждут квитирования">
      {!unacked ? (
        <p class="text-sm" style="color:var(--text-muted)">
          Загрузка…
        </p>
      ) : unacked.items.length === 0 ? (
        <p class="text-sm" style="color:var(--text-muted)">
          Неквитированных уведомлений нет.
        </p>
      ) : (
        <ul class="flex flex-col text-sm -mx-2">
          {unacked.items.map((n) => (
            <li key={n.id}>
              <a
                href={`/objects/${n.section_id}`}
                class="list-link flex justify-between items-baseline gap-3 px-2 py-1.5 rounded-md"
              >
                <span class="font-semibold">{n.object_name}</span>
                <span class="num shrink-0 text-right" style="color:var(--text-secondary)">
                  <b style="color:var(--text-primary)">{процент(n.probability)}</b> за {n.horizon_h}{' '}
                  ч · {formatDateTime(n.reported_at)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function Tile({
  label,
  value,
  sub,
  note,
  warn,
  accent,
  href,
}: {
  label: string
  value: string
  sub?: string
  note?: string
  warn?: string
  accent?: string // цвет полоски слева — у плиток, где число требует действия
  href?: string // плитка ведёт туда, где с этим числом работают
}) {
  return (
    <article
      class={`card flex flex-col gap-1 relative${href ? ' tile-link' : ''}`}
      style={`padding:14px 16px${accent ? `; border-left:4px solid ${accent}` : ''}`}
    >
      <h3 class="text-[13px] font-semibold" style="color:var(--text-secondary)">
        {label}
      </h3>
      <div
        class="num text-[28px] leading-tight font-bold"
        style="font-family:var(--font-display); letter-spacing:-0.01em"
      >
        {value}
      </div>
      {sub && (
        <div class="text-xs" style="color:var(--text-secondary)">
          {sub}
        </div>
      )}
      {note && (
        <div class="text-xs" style="color:var(--text-muted)">
          {note}
        </div>
      )}
      {warn && (
        <div class="text-xs font-semibold" style="color:var(--state-warning)">
          {warn}
        </div>
      )}
      {href && <a href={href} class="absolute inset-0" aria-label={`${label}: ${value}`} />}
    </article>
  )
}
