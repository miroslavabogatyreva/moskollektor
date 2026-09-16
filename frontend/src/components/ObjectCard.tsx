import { useEffect, useState } from 'preact/hooks'
import { DIRECTION_LABEL, type Direction } from '../lib/direction'

/* Карточка объекта — задача 5.5 (MOS-52). Открывают дашборд, схема и журнал
   по клику на маршрут /objects/:sectionId. Форма ответа GET /api/objects/{id}
   снята оркестратором с боевого контура 16.09.2026, поля ниже не выдуманы.

   Два computed_at в одном ответе значат разное, и подписи это разводят:
   current_risk.computed_at — момент среза данных (снимок выгрузки заказчика),
   recent_forecasts[].computed_at — время расчёта (когда прогон действительно шёл). */

interface Channel {
  channel_id: number
  tag: string
  name: string
  system_kind: string
  sensor_kind: string
}

interface CurrentRisk {
  run_id: number
  probability: number
  risk_rank: number
  horizon_h: number
  computed_at: string
  is_stale: boolean
  direction: Direction
  explanation_ru: string | null
}

interface RecentForecast {
  forecast_id: number
  direction: Direction
  probability: number
  risk_rank: number
  explanation_ru: string | null
  computed_at: string
}

interface ObjectDetail {
  section_id: number
  smvu_key: string
  inventory_no: string | null
  channels: Channel[]
  current_risk: CurrentRisk | null
  recent_forecasts: RecentForecast[]
}

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

export function ObjectCard({ sectionId }: { sectionId?: string } & Record<string, unknown>) {
  const [data, setData] = useState<ObjectDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!sectionId) return
    setData(null)
    setNotFound(false)
    setError(null)
    fetch(`/api/objects/${sectionId}`, { headers: { 'X-User-Login': API_LOGIN } })
      .then((r) => {
        if (r.status === 404) {
          setNotFound(true)
          return null
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<ObjectDetail>
      })
      .then((d) => d && setData(d))
      .catch((e) => setError(String(e)))
  }, [sectionId])

  if (notFound) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">Участок {sectionId} не найден.</p>
      </main>
    )
  }
  if (error) {
    return (
      <main class="p-5">
        <p style="color:var(--state-error)">Не удалось загрузить участок: {error}</p>
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

  const risk = data.current_risk
  const explanationLines = risk?.explanation_ru ? risk.explanation_ru.split('\n') : []

  return (
    <main class="p-5 flex flex-col gap-5">
      <div>
        <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
          Участок {data.section_id}
        </h1>
        <p style="color:var(--text-secondary)">
          Ключ СМВУ <code class="num">{data.smvu_key}</code>
          {data.inventory_no && (
            <>
              , инвентарный номер <span class="num">{data.inventory_no}</span>
            </>
          )}
        </p>
      </div>

      <section>
        <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
          Паспорт: каналы участка
        </h2>
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Тег', 'Название', 'Система', 'Тип датчика'].map((h) => (
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
            {data.channels.map((c) => (
              <tr key={c.channel_id} style="border-bottom:1px solid var(--border-subtle)">
                <td class="px-2 py-2 num">{c.tag}</td>
                <td class="px-2 py-2">{c.name}</td>
                <td class="px-2 py-2">{c.system_kind}</td>
                <td class="px-2 py-2">{c.sensor_kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {risk && (
        <section>
          <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
            Уровень риска
          </h2>
          <div class="text-sm flex flex-col gap-1">
            <div>
              {DIRECTION_LABEL[risk.direction]}: вероятность <b class="num">{risk.probability.toFixed(4)}</b>, ранг{' '}
              <b class="num">{risk.risk_rank}</b>, горизонт {risk.horizon_h} ч
              {risk.is_stale && <span style="color:var(--state-warning)"> · устарело</span>}
            </div>
            <div style="color:var(--text-secondary)">
              Данные по состоянию на {new Date(risk.computed_at).toLocaleDateString('ru-RU')} — момент среза
              выгрузки заказчика, не время расчёта
            </div>
          </div>

          {explanationLines.length > 0 && (
            <div class="text-sm p-3 mt-2 rounded" style="background:var(--bg-surface); border-left:3px solid var(--brand)">
              <div class="text-xs uppercase tracking-wide mb-1" style="color:var(--text-muted)">
                Почему такой риск
              </div>
              {explanationLines.map((line, i) => (
                <p key={i} class="m-0">
                  {line}
                </p>
              ))}
            </div>
          )}
        </section>
      )}

      <section>
        <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
          Последние прогнозы
        </h2>
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Время расчёта', 'Направление', 'Вероятность', 'Ранг'].map((h) => (
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
            {data.recent_forecasts.map((f) => (
              <tr key={f.forecast_id} style="border-bottom:1px solid var(--border-subtle)">
                <td class="px-2 py-2 num">{new Date(f.computed_at).toLocaleString('ru-RU')}</td>
                <td class="px-2 py-2">{DIRECTION_LABEL[f.direction]}</td>
                <td class="px-2 py-2 num">{f.probability.toFixed(4)}</td>
                <td class="px-2 py-2 num">{f.risk_rank}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.recent_forecasts.length === 0 && <p style="color:var(--text-muted)">Прогнозов по участку нет.</p>}
      </section>
    </main>
  )
}
