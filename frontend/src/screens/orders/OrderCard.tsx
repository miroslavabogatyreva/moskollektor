import type { ComponentChildren } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchOrder, fetchTopChannel } from './api'
import { PRIORITY_LABEL, type OrderDetail, type TopChannel } from './types'
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
  // Канал, который проверять (US-22 сц. 1): чаще других каналов участка терял связь.
  // undefined — ещё грузится, null — каналов у участка нет, 'ошибка' — не загрузился:
  // карточка заявки открывается и без него.
  const [канал, setКанал] = useState<TopChannel | null | 'ошибка' | undefined>(undefined)

  useEffect(() => {
    const sid = data?.object.section_id
    if (sid == null) return
    let отменено = false
    setКанал(undefined)
    fetchTopChannel(sid)
      .then((к) => {
        if (!отменено) setКанал(к)
      })
      .catch(() => {
        if (!отменено) setКанал('ошибка')
      })
    return () => {
      отменено = true
    }
  }, [data?.object.section_id])

  useEffect(() => {
    if (!orderId) return
    // Флажок отмены — та же гонка, что в ForecastCard.tsx (MOS-178): без него
    // ответ прошлого orderId, пришедший позже ответа нового, тихо подменяет
    // карточку — на экране целая заявка, но не та, что в адресе.
    let отменено = false
    setData(null)
    setNotFound(false)
    setError(null)
    fetchOrder(orderId)
      .then((d) => {
        if (!отменено) d ? setData(d) : setNotFound(true)
      })
      .catch((e) => {
        if (!отменено) setError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
  }, [orderId])

  if (notFound) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">
          Заявка {orderId} не найдена или вне вашей области видимости.
        </p>
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
        <Field label="Канал">
          <ChannelLine канал={канал} sectionId={data.object.section_id} />
        </Field>
        {/* Вид работ расчёт выбирает по классу критичности участка
            (backend/app/domain/order_rules.py, ВИД_РАБОТ) — этот показатель и стоит
            рядом, с причиной класса и вероятностью, из-за которой заявка заведена
            (US-22 сц. 2, Ф-73). */}
        <Field label="Вид работ">
          {data.work_type.activity_type_name}
          <div style="color:var(--text-muted)">
            выбран по классу критичности участка «{data.object.criticality_code}»:{' '}
            {data.object.criticality_reason}; вероятность потери связи за{' '}
            <span class="num">{data.forecast.horizon_h}</span> ч —{' '}
            <span class="num">{data.forecast.probability.toFixed(3).replace('.', ',')}</span>
          </div>
        </Field>
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
        {data.external_status && (
          <Field label="Статус в системе учёта">
            {data.external_status}
            {data.external_assignee && <> · {data.external_assignee}</>}
            {data.external_status_at && (
              <span style="color:var(--text-muted)">
                {' '}
                · с {formatDateTime(data.external_status_at)} (получено из системы учёта)
              </span>
            )}
          </Field>
        )}
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

function ChannelLine({
  канал,
  sectionId,
}: {
  канал: TopChannel | null | 'ошибка' | undefined
  sectionId: number
}) {
  if (канал === undefined) return <span style="color:var(--text-muted)">Загрузка…</span>
  if (канал === 'ошибка') {
    return <span style="color:var(--text-muted)">не удалось загрузить каналы участка</span>
  }
  if (канал === null || канал.faults_cnt === 0) {
    return <span>каналы участка связь не теряли — проверять участок целиком</span>
  }
  return (
    <>
      {канал.name.trim()} · {канал.sensor_kind}{' '}
      <span style="color:var(--text-muted)">
        ({канал.system_kind}) · чаще других каналов участка терял связь:{' '}
        <span class="num">{канал.faults_cnt}</span> раз
        {канал.last_fault_at && <>, последний {formatDateTime(канал.last_fault_at)}</>}
      </span>{' '}
      <a
        href={`/objects/${sectionId}?channel=${канал.channel_id}`}
        onClick={(e) => {
          e.preventDefault()
          route(`/objects/${sectionId}?channel=${канал.channel_id}`)
        }}
        style="color:var(--link)"
      >
        История канала на участке
      </a>
    </>
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
