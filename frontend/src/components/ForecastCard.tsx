import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { DIRECTION_LABEL, type Direction } from '../lib/direction'
import { errorMessage, formatDateTime } from '../lib/format'

/* Карточка прогноза — задача 6.6 (MOS-61). До этой задачи адресуемого экрана
   на forecast_id не было вовсе: прогнозы жили только внутри карточки объекта
   (ObjectCard, таблица «Последние прогнозы») и строкой журнала. GET /api/forecasts/{id}
   существует (backend/app/api/routes.py, задача 6.5, MOS-60). Блок ниже
   различает три состояния order_ids: поля нет вовсе (undefined) — метод ещё
   не переписан, честная надпись про это; пустой массив — заявок действительно
   нет; непустой — список ссылок. Первые два выглядят одинаково для человека,
   но означают разное, и подменять одно другим нельзя (нашла 58, 17.09.2026). */

interface ForecastDetail {
  forecast_id: number
  section_id: number
  direction: Direction
  horizon_h: number
  probability: number
  risk_rank: number
  as_of: string
  computed_at: string
  order_ids?: number[]
}

const API_LOGIN = 'dispatcher1'

export function ForecastCard({ forecastId }: { forecastId?: string } & Record<string, unknown>) {
  const [data, setData] = useState<ForecastDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!forecastId) return
    // Флажок отмены — та же гонка, что ab нашла в ObjectCard.tsx (22.09.2026):
    // без него ответ прошлого forecastId, пришедший позже ответа нового, тихо
    // подменяет карточку — на экране целый прогноз, но не тот, что в адресе.
    let отменено = false
    setData(null)
    setNotFound(false)
    setError(null)
    fetch(`/api/forecasts/${forecastId}`, { headers: { 'X-User-Login': API_LOGIN } })
      .then((r) => {
        if (r.status === 404) {
          if (!отменено) setNotFound(true)
          return null
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<ForecastDetail>
      })
      .then((d) => {
        if (!отменено && d) setData(d)
      })
      .catch((e) => {
        if (!отменено) setError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
  }, [forecastId])

  if (notFound) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">Прогноз {forecastId} не найден.</p>
      </main>
    )
  }
  if (error) {
    return (
      <main class="p-5">
        <p style="color:var(--state-error)">Не удалось загрузить прогноз: {error}</p>
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

  const orderIds = data.order_ids ?? []

  return (
    <main class="p-5 flex flex-col gap-5">
      <div>
        <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
          Прогноз
        </h1>
        <p style="color:var(--text-secondary)">
          Срез данных {formatDateTime(data.as_of)} · расчёт {formatDateTime(data.computed_at)}
        </p>
        <p style="color:var(--text-secondary)">
          Участок{' '}
          <a
            href={`/objects/${data.section_id}`}
            onClick={(e) => {
              e.preventDefault()
              route(`/objects/${data.section_id}`)
            }}
            class="num"
            style="color:var(--link)"
          >
            {data.section_id}
          </a>
        </p>
      </div>

      <section class="text-sm">
        {DIRECTION_LABEL[data.direction]}: вероятность{' '}
        <b class="num">{data.probability.toFixed(4)}</b>, ранг <b class="num">{data.risk_rank}</b>,
        горизонт {data.horizon_h} ч
        <p style="color:var(--text-secondary)">
          Вероятность отказа на коллекторе; локальный риск участка не оценён.
        </p>
        <p style="color:var(--text-secondary)">
          Факторы деревьев — не полное разложение итоговой вероятности.
        </p>
      </section>

      <section>
        <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
          Заявки по этому прогнозу
        </h2>
        {data.order_ids === undefined ? (
          <p class="text-sm" style="color:var(--text-muted)">
            Метод GET /api/forecasts/{'{id}'} пока не отдаёт связанные заявки.
          </p>
        ) : orderIds.length === 0 ? (
          <p class="text-sm" style="color:var(--text-muted)">
            Заявок по этому прогнозу нет.
          </p>
        ) : (
          <ul class="text-sm flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
            {orderIds.map((id) => (
              <li key={id}>
                <a
                  href={`/orders/${id}`}
                  onClick={(e) => {
                    e.preventDefault()
                    route(`/orders/${id}`)
                  }}
                  style="color:var(--link)"
                >
                  Заявка №{id}
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}
