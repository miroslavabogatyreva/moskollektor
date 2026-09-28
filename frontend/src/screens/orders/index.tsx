import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { ackNotification, fetchOrders, fetchUnackedNotifications } from './api'
import { usePoll, свежо } from '../../lib/poll'
import { PRIORITY_LABEL, STATUS_LABEL, type OrderListItem, type UnackedNotification } from './types'
import { OrderStatusBadge, PriorityBadge } from '../../components/Badge'
import { errorMessage, formatDateTime } from '../../lib/format'
import { rowLink, SkipTable } from '../../lib/a11y'
import { isoDate } from '../../components/ObjectCard.logic'
import { сПараметром } from '../../lib/sensorRisk'
import { моментРасчёта } from '../dashboard/api'

/* Экран заявок — задача 6.7 (MOS-62), постраничность — 4.13 (MOS-117). Данные
   читаются из GET /api/orders, форма ответа — contracts/examples/orders/order-list.json
   (moskollektor-44, 17.09.2026): узкий список, полная карточка — отдельным
   запросом по клику. Клик по строке ведёт на /orders/:id (карточка заявки, тот же тикет).
   План на неделю (US-18): список идёт по сроку, ближайший сверху; период срока
   отбирает сервер (GET /api/orders?due_from&due_to), поэтому число строк равно
   total ответа; просроченная заявка подписана словом «просрочено», не только цветом. */

const PRIORITY_BORDER: Record<string, string> = {
  '1': 'var(--state-error)',
  '2': 'var(--state-warning)',
  '3': 'transparent',
  '4': 'transparent',
}

const PAGE_SIZE = 200 // умолчание backend/app/api/orders.py::list_orders
const СУТКИ_МС = 24 * 3600 * 1000

// Просрочена — срок прошёл, а заявка не закрыта и не отменена
// (backend/app/domain/state_machine.py: COMPLETED и CANCELLED — конечные).
function просрочена(o: OrderListItem, сейчас: number): boolean {
  return Date.parse(o.due_at) < сейчас && o.status !== 'COMPLETED' && o.status !== 'CANCELLED'
}

// «просрочено на 119 сут.» — число суток целиком, меньше суток — «меньше суток»:
// одно слово «просрочено» не отличало вчерашнюю заявку от июньской (MOS-130).
function насколько(o: OrderListItem, сейчас: number): string {
  const сутки = Math.floor((сейчас - Date.parse(o.due_at)) / СУТКИ_МС)
  return сутки >= 1 ? `просрочено на ${сутки} сут.` : 'просрочено меньше суток'
}

const TABS = [
  { id: 'orders', label: 'Все заявки' },
  { id: 'unacked', label: 'Неквитированные' },
] as const
type TabId = (typeof TABS)[number]['id']

// Отбор по статусу и приоритету живёт в адресе (?status=&priority=), как уровень
// на дашборде: ссылка открывает тот же отбор, плитка «Заявки в работе» на главной
// ведёт на ?status=active. Значения — те, что принимает GET /api/orders: статусы
// из STATUS_LABEL (CHECK maint.notification.status) плюс группа active, коды
// приоритета из PRIORITY_LABEL (сид ref.priority). Чужое значение в адресе — как
// «все»: сервер ответил бы на него 422, а ссылка с опечаткой не должна ломать экран.
const СТАТУС_ОТБОРА: Record<string, string> = { active: 'Открытые и в работе', ...STATUS_LABEL }
const изАдреса = (v: unknown, словарь: Record<string, string>) =>
  typeof v === 'string' && v in словарь ? v : ''

export function OrdersScreen({
  status,
  priority,
}: { status?: string; priority?: string } & Record<string, unknown>) {
  const [tab, setTab] = useState<TabId>('orders')

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)">Заявки на превентивное обслуживание</h1>

      <div role="tablist" aria-label="Вкладки заявок" class="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`orders-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`orders-panel-${t.id}`}
            onClick={() => setTab(t.id)}
            class="tab"
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`orders-panel-${tab}`} aria-labelledby={`orders-tab-${tab}`}>
        {tab === 'orders' ? (
          <OrdersTab
            status={изАдреса(status, СТАТУС_ОТБОРА)}
            priority={изАдреса(priority, PRIORITY_LABEL)}
          />
        ) : (
          <UnackedTab />
        )}
      </div>
    </main>
  )
}

function OrdersTab({ status, priority }: { status: string; priority: string }) {
  const [items, setItems] = useState<OrderListItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [dueFrom, setDueFrom] = useState('')
  const [dueTo, setDueTo] = useState('')
  // Поиск по номеру: id заявки целиком или часть номера AF…/AW… (параметр q).
  const [q, setQ] = useState('')
  // Та же страница — раз в минуту от общего опроса (НФ-89, MOS-123). Вкладка
  // «Неквитированные» не опрашивается: она копит страницы «Показать ещё»,
  // и перезапрос первой страницы выбросил бы подгруженные.
  const { tick } = usePoll()
  // Другой отбор — с первой страницы: вторая страница «выполненных» обычно пуста.
  useEffect(() => setOffset(0), [status, priority])
  // «Назад» возвращает прежний отбор, как на дашборде.
  const отбор = (ключ: 'status' | 'priority', v: string) =>
    route(сПараметром('/orders', window.location.search, ключ, v || null))

  useEffect(() => {
    // Флажок отмены — та же гонка, что в ObjectCard.tsx (MOS-178): offset
    // в зависимостях перезапускает эффект на каждое «дальше», и ответ
    // прошлой страницы, пришедший позже нового, клал чужие строки в таблицу.
    let отменено = false
    fetchOrders(offset, dueFrom, dueTo, q, status, priority)
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
  }, [offset, tick, dueFrom, dueTo, q, status, priority])

  function период(from: string, to: string) {
    setDueFrom(from)
    setDueTo(to)
    setOffset(0)
  }
  // Пока срез расчёта не пришёл, просрочку не показываем: иначе на миг мелькнёт
  // «просрочено» от часов браузера (см. моментРасчёта).
  const [срез, setСрез] = useState<number | null>(null)
  useEffect(() => {
    моментРасчёта().then(setСрез)
  }, [tick])
  const сейчас = срез ?? -Infinity
  const стильПоля =
    'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

  return (
    <>
      {error && <p style="color:var(--state-error)">Не удалось загрузить заявки: {error}</p>}

      <div
        data-tour="orders-search"
        class="flex flex-wrap items-end gap-3 text-sm mb-3"
        style="color:var(--text-secondary)"
      >
        <label class="flex flex-col gap-1">
          Номер заявки
          <input
            type="search"
            value={q}
            placeholder="7562 или AF0001061588"
            onInput={(e) => {
              setQ((e.target as HTMLInputElement).value)
              setOffset(0)
            }}
            maxLength={40}
            class="px-2 py-1 rounded text-sm"
            style={стильПоля}
          />
        </label>
        <label class="flex flex-col gap-1">
          Статус
          <select
            value={status}
            onChange={(e) => отбор('status', (e.target as HTMLSelectElement).value)}
            class="px-2 py-1 rounded text-sm"
            style={стильПоля}
          >
            <option value="">Все</option>
            {Object.entries(СТАТУС_ОТБОРА).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label class="flex flex-col gap-1">
          Приоритет
          <select
            value={priority}
            onChange={(e) => отбор('priority', (e.target as HTMLSelectElement).value)}
            class="px-2 py-1 rounded text-sm"
            style={стильПоля}
          >
            <option value="">Все</option>
            {Object.entries(PRIORITY_LABEL).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label class="flex flex-col gap-1">
          Срок с
          <input
            type="date"
            value={dueFrom}
            onInput={(e) => период((e.target as HTMLInputElement).value, dueTo)}
            class="px-2 py-1 rounded text-sm"
            style={стильПоля}
          />
        </label>
        <label class="flex flex-col gap-1">
          Срок по
          <input
            type="date"
            value={dueTo}
            onInput={(e) => период(dueFrom, (e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm"
            style={стильПоля}
          />
        </label>
        <button
          type="button"
          onClick={() => период(isoDate(new Date()), isoDate(new Date(Date.now() + 6 * СУТКИ_МС)))}
          class="btn btn-secondary"
        >
          7 дней вперёд
        </button>
        {(dueFrom || dueTo) && (
          <button type="button" onClick={() => период('', '')} class="btn btn-secondary">
            Все сроки
          </button>
        )}
        {items !== null && (
          <span data-testid="orders-count">
            {q.trim() || status || priority
              ? 'найдено заявок'
              : dueFrom || dueTo
                ? 'заявок в периоде'
                : 'заявок'}
            : {total}
          </span>
        )}
      </div>

      <SkipTable targetId="orders-table-end" />
      <div class="card p-0 overflow-x-auto">
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['№', 'Объект', 'Вид работ', 'Срок', 'Реакция', 'Статус'].map((h) => (
                <th
                  key={h}
                  // Список всегда идёт по сроку, ближайший сверху (ORDER BY n.due_at
                  // в backend/app/api/orders.py) — заголовок это и называет (US-18 сц. 1).
                  aria-sort={h === 'Срок' ? 'ascending' : undefined}
                  class="th"
                >
                  {h === 'Срок' ? 'Срок ↑' : h}
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
                  <span class="font-semibold">{o.object_name}</span>{' '}
                  <span style="color:var(--text-muted)" class="num">
                    · {o.smvu_key}
                  </span>
                </td>
                <td class="px-2 py-2">{o.work_type_name}</td>
                <td class="px-2 py-2 num">
                  {formatDateTime(o.due_at)}
                  {просрочена(o, сейчас) && (
                    <span class="font-semibold" style="color:var(--state-error)">
                      {' '}
                      · {насколько(o, сейчас)}
                    </span>
                  )}
                </td>
                <td class="px-2 py-2 num">{o.deadline_hours.toFixed(1)} ч</td>
                <td class="px-2 py-2">
                  <span class="inline-flex flex-wrap gap-1.5">
                    <OrderStatusBadge status={o.status} />
                    <PriorityBadge code={o.priority_code} />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div id="orders-table-end" tabindex={-1} />

      {items === null && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {items !== null && items.length === 0 && (
        <p style="color:var(--text-muted)">
          {status || priority || q.trim()
            ? 'По этому отбору заявок нет.'
            : dueFrom || dueTo
              ? 'Заявок со сроком в этом периоде нет.'
              : 'Заявок пока нет.'}
        </p>
      )}

      {items !== null && total > 0 && (
        <div class="flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            class="btn btn-secondary"
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
            class="btn btn-secondary"
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

      <div class="card p-0 overflow-x-auto">
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Объект', 'Вероятность', 'Горизонт', ''].map((h) => (
                <th key={h} class="th">
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
                    class="btn btn-secondary"
                  >
                    Квитировать
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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
              class="btn btn-secondary"
            >
              Показать ещё
            </button>
          )}
        </div>
      )}
    </>
  )
}
