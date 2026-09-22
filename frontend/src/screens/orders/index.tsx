import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchOrders } from './api'
import { PRIORITY_LABEL, type OrderListItem } from './types'
import { errorMessage, formatDateTime } from '../../lib/format'
import { rowLink, SkipTable } from '../../lib/a11y'

/* Экран заявок — задача 6.7 (MOS-62), постраничность — 4.13 (MOS-117). Данные
   читаются из GET /api/orders, форма ответа — contracts/examples/orders/order-list.json
   (moskollektor-44, 17.09.2026): узкий список, полная карточка — отдельным
   запросом по клику. Фильтров нет — они в части III (docs/acceptance-test.md),
   сюда не входят. Клик по строке ведёт на /orders/:id (карточка заявки, тот же тикет). */

const PRIORITY_BORDER: Record<string, string> = {
  '1': 'var(--state-error)',
  '2': 'var(--state-warning)',
  '3': 'transparent',
  '4': 'transparent',
}

const PAGE_SIZE = 200 // умолчание backend/app/api/orders.py::list_orders

export function OrdersScreen(_props: Record<string, unknown>) {
  const [items, setItems] = useState<OrderListItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchOrders(offset)
      .then((r) => {
        setItems(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [offset])

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Заявки на превентивное обслуживание
      </h1>

      {error && <p style="color:var(--state-error)">Не удалось загрузить заявки: {error}</p>}

      <SkipTable targetId="orders-table-end" />
      <table class="w-full text-sm" style="border-collapse:collapse">
        <thead>
          <tr>
            {['№', 'Объект', 'Вид работ', 'Срок', 'Запас', 'Статус'].map((h) => (
              <th
                key={h}
                class="text-left px-2 py-2 text-xs uppercase tracking-wide"
                style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items?.map((o) => (
            <tr
              key={o.id}
              {...rowLink(() => route(`/orders/${o.id}`))}
              style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${PRIORITY_BORDER[o.priority_code] ?? 'transparent'}; cursor:pointer`}
            >
              <td class="px-2 py-2 num">{o.id}</td>
              <td class="px-2 py-2">
                {o.object_name}{' '}
                <span style="color:var(--text-muted)" class="num">
                  · {o.smvu_key}
                </span>
              </td>
              <td class="px-2 py-2">{o.work_type_name}</td>
              <td class="px-2 py-2 num">{formatDateTime(o.due_at)}</td>
              <td class="px-2 py-2 num">{o.lead_hours.toFixed(1)} ч</td>
              <td class="px-2 py-2">
                {o.status}{' '}
                <span style="color:var(--text-muted)">
                  · {PRIORITY_LABEL[o.priority_code] ?? o.priority_code}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div id="orders-table-end" tabindex={-1} />

      {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {items !== null && items.length === 0 && (
        <p style="color:var(--text-muted)">Заявок пока нет.</p>
      )}

      {items !== null && total > 0 && (
        <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            class="px-2 py-1 rounded disabled:opacity-50"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            ← Раньше
          </button>
          <span>
            {offset + 1}–{Math.min(offset + items.length, total)} из {total}
          </span>
          <button
            type="button"
            disabled={offset + items.length >= total}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            class="px-2 py-1 rounded disabled:opacity-50"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            Позже →
          </button>
        </div>
      )}
    </main>
  )
}
