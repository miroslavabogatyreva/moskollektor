import { formatDateTime } from '../lib/format'
import { процент } from '../screens/dashboard/rows'
import type { Unacked } from '../screens/dashboard/types'

/* Плитка, панель и журнал «Ждут квитирования» — общие для дашборда и главной
   (схема пикетов, MOS-265): одна вёрстка и одни подписи на обоих экранах. */

export function Panel({ title, children }: { title: string; children: preact.ComponentChildren }) {
  return (
    <section
      class="p-3 rounded flex flex-col gap-2"
      style="background:var(--bg-surface); border:1px solid var(--border-subtle)"
    >
      <h2 class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {title}
      </h2>
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
        <ul class="flex flex-col gap-2 text-sm">
          {unacked.items.map((n) => (
            <li key={n.id}>
              <a
                href={`/objects/${n.section_id}`}
                class="flex justify-between gap-3"
                style="color:inherit; text-decoration:none"
              >
                <span>{n.object_name}</span>
                <span class="num shrink-0" style="color:var(--text-secondary)">
                  {процент(n.probability)} за {n.horizon_h} ч · {formatDateTime(n.reported_at)}
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
      class="p-3 rounded flex flex-col gap-1 relative"
      style={`background:var(--bg-surface); border:1px solid var(--border-subtle)${accent ? `; border-left:4px solid ${accent}` : ''}`}
    >
      <h3 class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {label}
      </h3>
      <div class="num text-2xl font-semibold" style="font-family:var(--font-display)">
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
        <div class="text-xs" style="color:var(--state-warning)">
          {warn}
        </div>
      )}
      {href && <a href={href} class="absolute inset-0" aria-label={`${label}: ${value}`} />}
    </article>
  )
}
