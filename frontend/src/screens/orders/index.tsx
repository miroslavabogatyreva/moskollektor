import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { ackNotification, fetchOrders, fetchUnackedNotifications } from './api'
import { usePoll, свежо } from '../../lib/poll'
import { PRIORITY_LABEL, type OrderListItem, type UnackedNotification } from './types'
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

const TABS = [
  { id: 'orders', label: 'Все заявки' },
  { id: 'unacked', label: 'Неквитированные' },
] as const
type TabId = (typeof TABS)[number]['id']

export function OrdersScreen(_props: Record<string, unknown>) {
  const [tab, setTab] = useState<TabId>('orders')

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Заявки на превентивное обслуживание
      </h1>

      <div
        role="tablist"
        aria-label="Вкладки заявок"
        class="flex gap-2"
        style="border-bottom:1px solid var(--border-subtle)"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`orders-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`orders-panel-${t.id}`}
            onClick={() => setTab(t.id)}
            class="px-3 py-2 text-sm"
            style={`border-bottom:2px solid ${tab === t.id ? 'var(--brand)' : 'transparent'}; color:var(--text-${tab === t.id ? 'primary' : 'muted'})`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`orders-panel-${tab}`} aria-labelledby={`orders-tab-${tab}`}>
        {tab === 'orders' ? <OrdersTab /> : <UnackedTab />}
      </div>
    </main>
  )
}

function OrdersTab() {
  const [items, setItems] = useState<OrderListItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  // Та же страница — раз в минуту от общего опроса (НФ-89, MOS-123). Вкладка
  // «Неквитированные» не опрашивается: она копит страницы «Показать ещё»,
  // и перезапрос первой страницы выбросил бы подгруженные.
  const { tick } = usePoll()

  useEffect(() => {
    // Флажок отмены — та же гонка, что в ObjectCard.tsx (MOS-178): offset
    // в зависимостях перезапускает эффект на каждое «дальше», и ответ
    // прошлой страницы, пришедший позже нового, клал чужие строки в таблицу.
    let отменено = false
    fetchOrders(offset)
      .then((r) => {
        if (!отменено) {
          setItems(r.items)
          setTotal(r.total)
          свежо()
        }
      })
      .catch((e) => {
        if (!отменено) setError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
  }, [offset, tick])

  return (
    <>
      {error && <p style="color:var(--state-error)">Не удалось загрузить заявки: {error}</p>}

      <SkipTable targetId="orders-table-end" />
      <table class="w-full text-sm" style="border-collapse:collapse">
        <thead>
          <tr>
            {['№', 'Объект', 'Вид работ', 'Срок', 'Реакция', 'Статус'].map((h) => (
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
              <td class="px-2 py-2 num">{o.deadline_hours.toFixed(1)} ч</td>
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
    </>
  )
}

function UnackedTab() {
  const [items, setItems] = useState<UnackedNotification[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ackError, setAckError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    let отменено = false
    fetchUnackedNotifications(offset)
      .then((r) => {
        if (отменено) return
        setItems((prev) => {
          if (offset === 0) return r.items
          // Дедуп по id (MOS-238, находка 0d): квитирование на предыдущей
          // странице сдвигает выдачу — offset у следующей страницы теперь
          // считаем от items.length, а не накопительно PAGE_SIZE'ами, но
          // поток проигрывания может и вставлять записи, а не только убирать,
          // так что дубль на стыке страниц исключать нужно с обеих сторон.
          const известные = new Set((prev ?? []).map((n) => n.id))
          return [...(prev ?? []), ...r.items.filter((n) => !известные.has(n.id))]
        })
        setTotal(r.total)
      })
      .catch((e) => {
        if (!отменено) setLoadError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
  }, [offset])

  async function квитировать(id: number) {
    setBusyId(id)
    setAckError(null)
    try {
      await ackNotification(id)
      setItems((prev) => (prev ? prev.filter((n) => n.id !== id) : prev))
      setTotal((t) => Math.max(0, t - 1))
    } catch (e) {
      setAckError(errorMessage(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <>
      {loadError && <p style="color:var(--state-error)">Не удалось загрузить: {loadError}</p>}
      {ackError && <p style="color:var(--state-error)">Не удалось квитировать: {ackError}</p>}

      <table class="w-full text-sm" style="border-collapse:collapse">
        <thead>
          <tr>
            {['Объект', 'Вероятность', 'Горизонт', ''].map((h) => (
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
          {items?.map((n) => (
            <tr
              key={n.id}
              data-notification-id={n.id}
              style="border-bottom:1px solid var(--border-subtle)"
            >
              <td class="px-2 py-2">
                {n.object_name}{' '}
                <span style="color:var(--text-muted)" class="num">
                  · {n.smvu_key}
                </span>
              </td>
              <td class="px-2 py-2 num">{(n.probability * 100).toFixed(0)} %</td>
              <td class="px-2 py-2 num">{n.horizon_h} ч</td>
              <td class="px-2 py-2">
                <button
                  type="button"
                  disabled={busyId === n.id}
                  onClick={() => квитировать(n.id)}
                  class="px-2 py-1 rounded disabled:opacity-50"
                  style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
                >
                  Квитировать
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {items === null && !loadError && <p style="color:var(--text-muted)">Загрузка…</p>}
      {items !== null && items.length === 0 && (
        <p style="color:var(--text-muted)">Неквитированных событий нет.</p>
      )}

      {items !== null && total > 0 && (
        <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
          <span>
            показано {items.length} из {total}
          </span>
          {items.length < total && (
            <button
              type="button"
              // items.length, не накопительный o + PAGE_SIZE (находка 0d,
              // MOS-238): квитирование убирает строку из items без изменения
              // offset — следующая страница обязана начинаться с того, что
              // экран показывает СЕЙЧАС, а не с арифметики прошлых кликов,
              // иначе ровно одна строка на стыке никогда не попадёт на экран.
              onClick={() => setOffset(items.length)}
              class="px-2 py-1 rounded"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            >
              Показать ещё
            </button>
          )}
        </div>
      )}
    </>
  )
}
