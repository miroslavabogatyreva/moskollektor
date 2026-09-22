import type { ComponentChildren } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchOrder } from './api'
import { PRIORITY_LABEL, type OrderDetail } from './types'
import { errorMessage, formatDateTime } from '../../lib/format'

/* Карточка заявки — задачи 6.6 и 6.7 (MOS-61, MOS-62). Форма ответа —
   contracts/examples/orders/order.json (moskollektor-44, 17.09.2026).
   Четыре поля М-11 — объект, вид работ, срок, обоснование — показаны
   первым блоком, не вперемешку со служебными. Ссылка на прогноз — М-12,
   ведёт на /forecasts/:id (ForecastCard, задача 6.6). Подписана «срез
   данных», не «прогноз от»: forecast.as_of — момент среза выгрузки,
   на которой считали, а не время самого расчёта (то же различие, что
   у ObjectCard.current_risk.computed_at) — на /forecasts/:id заголовок
   называет оба момента, и подписи должны совпадать, иначе клик по одной
   дате приводит на экран с другой (нашёл оркестратор, 17.09.2026). */

export function OrderCard({ orderId }: { orderId?: string } & Record<string, unknown>) {
  const [data, setData] = useState<OrderDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!orderId) return
    setData(null)
    setNotFound(false)
    setError(null)
    fetchOrder(orderId)
      .then((d) => (d ? setData(d) : setNotFound(true)))
      .catch((e) => setError(errorMessage(e)))
  }, [orderId])

  if (notFound) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">Заявка {orderId} не найдена.</p>
      </main>
    )
  }
  if (error) {
    return (
      <main class="p-5">
        <p style="color:var(--state-error)">Не удалось загрузить заявку: {error}</p>
      </main>
    )
  }
  if (!data) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">Загрузка…</p>
      </main>
    )
  }

  return (
    <main class="p-5 flex flex-col gap-5">
      <div>
        <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
          Заявка <span class="num">{data.notification_no}</span>
        </h1>
        <p style="color:var(--text-secondary)">{data.subject}</p>
      </div>

      <section class="text-sm flex flex-col gap-2">
        <Field label="Объект">
          {data.object.name} <span class="num">· {data.object.smvu_key}</span>
        </Field>
        <Field label="Вид работ">{data.work_type.activity_type_name}</Field>
        <Field label="Срок выполнения">
          {formatDateTime(data.due_at)}{' '}
          <span style="color:var(--text-muted)">
            · срок заявки <span class="num">{data.deadline_hours}</span> ч от обнаружения
          </span>
        </Field>
        {data.warning_opened_at && (
          <Field label="Предупреждение модели открыто">
            {formatDateTime(data.warning_opened_at)}
          </Field>
        )}
        {data.risk_window_end && (
          <Field label="Окно риска до">{formatDateTime(data.risk_window_end)}</Field>
        )}
        <Field label="Обоснование">{data.reason}</Field>
      </section>

      <section class="text-sm flex flex-col gap-1" style="color:var(--text-secondary)">
        <div>
          Статус <b>{data.status}</b> · приоритет{' '}
          <b>{PRIORITY_LABEL[data.priority.code] ?? data.priority.name}</b> (норматив реакции{' '}
          <span class="num">{data.priority.response_hours}</span> ч)
        </div>
        <div>
          Завёл: {data.created_by ?? 'расчёт'}, {formatDateTime(data.created_at)}
        </div>
      </section>

      <section>
        <a
          href={`/forecasts/${data.forecast.forecast_id}`}
          onClick={(e) => {
            e.preventDefault()
            route(`/forecasts/${data.forecast.forecast_id}`)
          }}
          class="text-sm"
          style="color:var(--link)"
        >
          Прогноз, срез данных {formatDateTime(data.forecast.as_of)}
        </a>
      </section>
    </main>
  )
}

function Field({ label, children }: { label: string; children: ComponentChildren }) {
  return (
    <div>
      <div class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {label}
      </div>
      <div>{children}</div>
    </div>
  )
}
