import { useEffect, useRef, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { apiFetch } from '../lib/api'
import { fetchMe } from '../lib/auth'
import { DIRECTION_LABEL, type Direction } from '../lib/direction'
import { errorMessage, formatDateTime, имяУчастка } from '../lib/format'
import { type Decision, VerdictDialog } from './VerdictDialog'
import { type Outcome, OutcomeDialog } from './OutcomeDialog'

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
  // Последнее решение диспетчера (MOS-55); null — прогноз ещё не разобран.
  decision?: Decision | null
  // Исход (US-10): null — никто не отметил; horizon_expired — окно прогноза позади.
  outcome?: Outcome | null
  horizon_expired?: boolean
}

export function ForecastCard({ forecastId }: { forecastId?: string } & Record<string, unknown>) {
  const [data, setData] = useState<ForecastDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const decideButton = useRef<HTMLButtonElement>(null)
  const [outcomeOpen, setOutcomeOpen] = useState(false)
  const outcomeButton = useRef<HTMLButtonElement>(null)
  // Кнопку решения видят только роли с правом forecasts.decide (миграция 052).
  // Сервер и так ответит технику 403 — это удобство, а не защита.
  // null — ответ /api/auth/me ещё не пришёл: E2E ждёт по data-can-decide именно
  // ответа, иначе «кнопки нет» проверялось бы раньше, чем она могла появиться.
  const [canDecide, setCanDecide] = useState<boolean | null>(null)
  // Ключ участка «коллектор:пикет» — из того же справочника, что у дашборда и журнала
  // (US-06 сц. 1, US-14): участок называется «Коллектор 847, пикет 1», а не номером.
  const [ключи, setКлючи] = useState<Map<number, string> | null>(null)
  useEffect(() => {
    fetch('/data/sections.json')
      .then((r) => (r.ok ? (r.json() as Promise<{ section_id: number; smvu_key: string }[]>) : []))
      .then((all) => setКлючи(new Map(all.map((x) => [x.section_id, x.smvu_key]))))
      .catch(() => setКлючи(new Map()))
  }, [])
  useEffect(() => {
    fetchMe()
      .then((me) =>
        setCanDecide(!!me?.roles.some((r) => r === 'dispatcher' || r === 'ods_dispatcher')),
      )
      .catch(() => setCanDecide(false))
  }, [])

  useEffect(() => {
    if (!forecastId) return
    // Флажок отмены — та же гонка, что ab нашла в ObjectCard.tsx (22.09.2026):
    // без него ответ прошлого forecastId, пришедший позже ответа нового, тихо
    // подменяет карточку — на экране целый прогноз, но не тот, что в адресе.
    let отменено = false
    setData(null)
    setNotFound(false)
    setError(null)
    apiFetch(`/api/forecasts/${forecastId}`)
      .then((r) => {
        // 403 — чужой объект или id вне области видимости (MOS-107): тому, кто видит
        // не весь парк, сервер не говорит, есть ли объект, поэтому текст у них общий.
        if (r.status === 404 || r.status === 403) {
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
        <p style="color:var(--text-muted)">
          Прогноз {forecastId} не найден или вне вашей области видимости.
        </p>
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
            style="color:var(--link)"
          >
            {ключи?.get(data.section_id) ? имяУчастка(ключи.get(data.section_id)!) : 'участок'}
          </a>{' '}
          <span class="num" style="color:var(--text-muted)">
            · {data.section_id}
          </span>
        </p>
      </div>

      <section class="text-sm">
        {DIRECTION_LABEL[data.direction]}: вероятность{' '}
        <b class="num">{data.probability.toFixed(4)}</b>, ранг <b class="num">{data.risk_rank}</b>,
        горизонт {data.horizon_h} ч
      </section>

      <section
        data-testid="last-decision"
        data-can-decide={canDecide == null ? undefined : String(canDecide)}
        class="text-sm flex flex-col gap-2 items-start"
      >
        <h2 class="font-semibold" style="color:var(--text-muted)">
          Решение диспетчера
        </h2>
        {data.decision ? (
          <p>
            <b>{data.decision.decision_name}</b>
            {data.decision.reason_name && <> · причина: {data.decision.reason_name}</>} ·{' '}
            {data.decision.decided_by}, {formatDateTime(data.decision.decided_at)}
            {data.decision.verified_externally && <> · проверено по внешним источникам</>}
            {data.decision.comment && (
              <span class="block" style="color:var(--text-secondary)">
                {data.decision.comment}
              </span>
            )}
          </p>
        ) : (
          <p style="color:var(--text-muted)">Решения по этому прогнозу ещё нет.</p>
        )}
        {canDecide && (
          <button
            ref={decideButton}
            type="button"
            onClick={() => setDialogOpen(true)}
            class="px-3 py-1 rounded"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            Решение диспетчера
          </button>
        )}
        {dialogOpen && (
          <VerdictDialog
            forecastId={data.forecast_id}
            onClose={() => {
              setDialogOpen(false)
              decideButton.current?.focus()
            }}
            onSaved={(d) => setData({ ...data, decision: d })}
          />
        )}
      </section>

      {/* Исход прогноза (US-10): чем прогноз кончился. Без отметки человека система
          исход не ставит — пишет «ещё открыт» или «горизонт истёк» (сц. 4, Ф-75). */}
      <section data-testid="outcome" class="text-sm flex flex-col gap-2 items-start">
        <h2 class="font-semibold" style="color:var(--text-muted)">
          Исход прогноза
        </h2>
        {data.outcome ? (
          <p>
            <b>{data.outcome.outcome_name}</b>
            {data.outcome.reason_name && <> · причина: {data.outcome.reason_name}</>} ·{' '}
            {data.outcome.decided_by}, {formatDateTime(data.outcome.decided_at)}
          </p>
        ) : (
          <p style="color:var(--text-muted)">
            {data.horizon_expired
              ? 'Исход не отмечен, горизонт истёк.'
              : 'Исход не отмечен, прогноз ещё открыт.'}
          </p>
        )}
        {canDecide && (
          <button
            ref={outcomeButton}
            type="button"
            onClick={() => setOutcomeOpen(true)}
            class="px-3 py-1 rounded"
            style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
          >
            Отметить исход
          </button>
        )}
        {outcomeOpen && (
          <OutcomeDialog
            forecastId={data.forecast_id}
            onClose={() => {
              setOutcomeOpen(false)
              outcomeButton.current?.focus()
            }}
            onSaved={(o) => setData({ ...data, outcome: o })}
          />
        )}
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
