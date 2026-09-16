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
  last_reading_at: string | null
  channels: Channel[]
  current_risk: CurrentRisk | null
  recent_forecasts: RecentForecast[]
}

interface Reading {
  read_time: string
  channel_id: number
  is_alarm: boolean
  value_text: string | null
  value_num: number | null
}

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

// Окно по умолчанию — 7 суток, оканчивающихся последней записью участка
// (last_reading_at из GET /api/objects/{id}), а не сегодняшней датой: у 54,4%
// участков за последние 7 суток выгрузки нет ни строки, данные могли замолчать
// задолго до конца выгрузки. Без last_reading_at (участок совсем без записей —
// сегодня таких нет) откатываемся на 7 суток от сегодня.
function defaultWindow(lastReadingAt: string | null): [string, string] {
  const end = lastReadingAt ? new Date(lastReadingAt) : new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 6)
  return [isoDate(start), isoDate(end)]
}

export function ObjectCard({ sectionId }: { sectionId?: string } & Record<string, unknown>) {
  const [data, setData] = useState<ObjectDetail | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Пустая строка = "дефолт ещё не посчитан от last_reading_at". Даты можно
  // подвинуть руками — тогда они больше не сбрасываются при смене участка.
  const [readFrom, setReadFrom] = useState('')
  const [readTo, setReadTo] = useState('')
  const [readings, setReadings] = useState<Reading[] | null>(null)
  const [readingsError, setReadingsError] = useState<string | null>(null)

  useEffect(() => {
    if (!sectionId) return
    setData(null)
    setNotFound(false)
    setError(null)
    setReadFrom('')
    setReadTo('')
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

  useEffect(() => {
    // Зависимость только от section_id: пересчитать дефолт при смене участка,
    // но не при каждом обновлении data (его тут больше не с чем сравнивать).
    if (!data) return
    const [from, to] = defaultWindow(data.last_reading_at)
    setReadFrom(from)
    setReadTo(to)
  }, [data?.section_id])

  useEffect(() => {
    if (!sectionId || !readFrom || !readTo) return
    setReadings(null)
    setReadingsError(null)
    fetch(`/api/objects/${sectionId}/readings?from=${readFrom}&to=${readTo}`, {
      headers: { 'X-User-Login': API_LOGIN },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Reading[]>
      })
      .then(setReadings)
      .catch((e) => setReadingsError(String(e)))
  }, [sectionId, readFrom, readTo])

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

      <section>
        <h2 class="text-sm font-semibold mb-1" style="color:var(--text-muted)">
          Показания датчиков
        </h2>
        <p class="text-sm mb-2" style="color:var(--text-secondary)">
          {data.last_reading_at
            ? `Последняя запись участка: ${new Date(data.last_reading_at).toLocaleString('ru-RU')}. Окно ниже подобрано вокруг неё.`
            : 'Записей по участку ещё не было — окно ниже за последние 7 суток от сегодня.'}
        </p>
        <div class="flex flex-wrap items-end gap-4 text-sm mb-3" style="color:var(--text-secondary)">
          <label class="flex flex-col gap-1">
            С даты
            <input
              type="date"
              value={readFrom}
              onInput={(e) => setReadFrom((e.target as HTMLInputElement).value)}
              class="px-2 py-1 rounded text-sm"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            />
          </label>
          <label class="flex flex-col gap-1">
            По дату
            <input
              type="date"
              value={readTo}
              onInput={(e) => setReadTo((e.target as HTMLInputElement).value)}
              class="px-2 py-1 rounded text-sm"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            />
          </label>
        </div>

        {readingsError && (
          <p style="color:var(--state-error)">Не удалось загрузить показания: {readingsError}</p>
        )}
        {readings === null && !readingsError && <p style="color:var(--text-muted)">Загрузка…</p>}

        {readings && (
          <div class="flex flex-col gap-4">
            {data.channels.map((c) => {
              const chReadings = readings
                .filter((r) => r.channel_id === c.channel_id)
                .sort((a, b) => a.read_time.localeCompare(b.read_time))
              const numericShare =
                chReadings.length === 0
                  ? 0
                  : chReadings.filter((r) => r.value_num != null).length / chReadings.length
              return (
                <div key={c.channel_id}>
                  <div class="text-xs uppercase tracking-wide mb-1" style="color:var(--text-muted)">
                    {c.name} · {c.sensor_kind}
                  </div>
                  {chReadings.length === 0 ? (
                    <p class="text-sm" style="color:var(--text-muted)">
                      Нет показаний за период.
                    </p>
                  ) : numericShare > 0.5 ? (
                    <NumericLine readings={chReadings} />
                  ) : (
                    <StateRibbon readings={chReadings} from={readFrom} to={readTo} />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>
    </main>
  )
}

// Числовой ряд — линия, но не через пропуск: соединяем только записи, идущие
// подряд в самой выгрузке. Между двумя числами у температурного канала может
// лежать "Отключено устройство" — отрезок через него показал бы работающий
// прибор там, где его выключили (доля числовых у канала берётся порогом 0,5
// в ObjectCard: одна случайная цифра среди состояний линию не включает).
function NumericLine({ readings }: { readings: Reading[] }) {
  const W = 1000
  const H = 70
  const PAD = 10
  const times = readings.map((r) => new Date(r.read_time).getTime())
  const values = readings.filter((r) => r.value_num != null).map((r) => r.value_num as number)
  const tMin = Math.min(...times)
  const tMax = Math.max(...times)
  const vMin = Math.min(...values)
  const vMax = Math.max(...values)
  const x = (t: number) => PAD + ((t - tMin) / (tMax - tMin || 1)) * (W - PAD * 2)
  const y = (v: number) => H - PAD - ((v - vMin) / (vMax - vMin || 1)) * (H - PAD * 2)

  let d = ''
  let penDown = false
  readings.forEach((r, i) => {
    if (r.value_num == null) {
      penDown = false
      return
    }
    const cmd = penDown ? 'L' : 'M'
    d += `${cmd}${x(times[i])},${y(r.value_num)} `
    penDown = true
  })

  return (
    <svg viewBox={`0 0 ${W} ${H}`} class="w-full" style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px">
      <path d={d} fill="none" stroke="var(--chart-outline)" stroke-width="4" stroke-linecap="round" />
      <path d={d} fill="none" stroke="var(--chart-6)" stroke-width="2" stroke-linecap="round" />
    </svg>
  )
}

// Нечисловой ряд — лента состояний: сегмент от одной записи до следующей,
// цвет по is_alarm (это настройка прибора, не решение человека — поэтому
// цвет служебный var(--state-warning), а не шкала риска и не слово "авария").
function StateRibbon({ readings, from, to }: { readings: Reading[]; from: string; to: string }) {
  const W = 1000
  const H = 36
  const winStart = new Date(from).getTime()
  const winEnd = new Date(to).getTime() + 24 * 3600 * 1000
  const x = (t: number) => ((t - winStart) / (winEnd - winStart || 1)) * W

  return (
    <svg viewBox={`0 0 ${W} ${H}`} class="w-full" style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px">
      {readings.map((r, i) => {
        const t0 = new Date(r.read_time).getTime()
        const t1 = i + 1 < readings.length ? new Date(readings[i + 1].read_time).getTime() : winEnd
        const x0 = x(t0)
        const width = Math.max(x(t1) - x0, 0.5)
        return (
          <rect
            key={i}
            x={x0}
            y={6}
            width={width}
            height={H - 12}
            fill={r.is_alarm ? 'var(--state-warning)' : 'var(--border-strong)'}
          >
            <title>{`${r.value_text ?? '—'} · ${new Date(r.read_time).toLocaleString('ru-RU')}`}</title>
          </rect>
        )
      })}
    </svg>
  )
}
