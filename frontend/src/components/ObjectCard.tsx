import { useEffect, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { apiFetch } from '../lib/api'
import { DIRECTION_LABEL, type Direction } from '../lib/direction'
import { errorMessage, formatDateTime, имяУчастка } from '../lib/format'
import { rowLink } from '../lib/a11y'
import {
  axisTicks,
  defaultWindow,
  fmtValue,
  groupChannelsBySystem,
  groupRepeatedForecasts,
  shortDate,
  type RecentForecast,
} from './ObjectCard.logic'

/* Карточка объекта — задача 5.5 (MOS-52). Открывают дашборд, схема и журнал
   по клику на маршрут /objects/:sectionId. Форма ответа GET /api/objects/{id}
   снята оркестратором с боевого контура 16.09.2026, поля ниже не выдуманы.

   Две даты в одном ответе значат разное, и с 21.09.2026 это видно по именам
   (MOS-118): current_risk.as_of — момент среза данных (снимок выгрузки заказчика),
   recent_forecasts[].computed_at — время расчёта (когда прогон действительно шёл).
   Раньше обе звались computed_at, и различить их можно было только по подписи. */

interface Channel {
  channel_id: number
  tag: string
  name: string
  system_kind: string
  sensor_kind: string
}

// GET /api/objects/{id}/channels (MOS-151, Q5.25) — не паспорт, а факт отказов
// по журналу: сколько раз канал уходил в "Неисправен"/"Неопределен" дольше часа,
// когда в последний раз и сколько в среднем лежит. faults_cnt = 0 — не пустая
// строка, а исправная линия: печатаем "отказов не было", а не вычёркиваем канал.
interface ChannelFaults {
  channel_id: number
  system_kind: string
  sensor_kind: string
  name: string
  is_active: boolean
  faults_cnt: number
  last_fault_at: string | null
  avg_duration_h: number | null
}

interface ChannelFaultsResponse {
  total: number
  items: ChannelFaults[]
}

interface CurrentRisk {
  run_id: number
  probability: number
  risk_rank: number
  horizon_h: number
  as_of: string
  is_stale: boolean
  direction: Direction
  explanation_ru: string | null
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

  const [channelFaults, setChannelFaults] = useState<ChannelFaults[] | null>(null)
  const [channelFaultsError, setChannelFaultsError] = useState<string | null>(null)

  useEffect(() => {
    if (!sectionId) return
    // Тот же класс гонки, что у эффекта показаний ниже, только тише: без
    // флажка ответ СТАРОГО участка, пришедший позже ответа нового, тихо
    // перезаписывает карточку — на экране целый, согласованный, но чужой
    // участок под чужим адресом, ни ошибки, ни пустого кадра (нашла ab,
    // 22.09.2026, на паузе 20мс между переходами, 2 раза из 9 попыток).
    let отменено = false
    setData(null)
    setNotFound(false)
    setError(null)
    setReadFrom('')
    setReadTo('')
    // Без этого при живой смене участка (без перезагрузки) один кадр рисует
    // показания СТАРОГО участка поверх пустого окна нового — эффект ниже видит
    // readFrom/readTo === '' и выходит, не тронув readings (нашла ab, 22.09.2026:
    // 12 NaN-rect и один RangeError на переходе 2204 → 2157).
    setReadings(null)
    apiFetch(`/api/objects/${sectionId}`)
      .then((r) => {
        // 403 — чужой объект или id вне области видимости (MOS-107): тому, кто видит
        // не весь парк, сервер не говорит, есть ли объект, поэтому текст у них общий.
        if (r.status === 404 || r.status === 403) {
          if (!отменено) setNotFound(true)
          return null
        }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<ObjectDetail>
      })
      .then((d) => {
        if (!отменено && d) setData(d)
      })
      .catch((e) => {
        if (!отменено) setError(errorMessage(e))
      })

    setChannelFaults(null)
    setChannelFaultsError(null)
    // limit=200: на участке бывает до 100 каналов (MOS-151), с запасом на вырост.
    apiFetch(`/api/objects/${sectionId}/channels?limit=200`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<ChannelFaultsResponse>
      })
      .then((d) => {
        if (!отменено) setChannelFaults(d.items)
      })
      .catch((e) => {
        if (!отменено) setChannelFaultsError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
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
    // ab поймала гонку и после починки зависимостей ниже — дважды вживую,
    // не смогла надёжно воспроизвести по заказу, окно в доли миллисекунды.
    // Флажок отменяет применение ответа, если этот же эффект успел
    // перезапуститься (новый readFrom/readTo или другой участок) раньше,
    // чем старый fetch вернулся: без него более старый ответ может лечь
    // поверх более нового состояния.
    let отменено = false
    setReadings(null)
    setReadingsError(null)
    apiFetch(`/api/objects/${sectionId}/readings?from=${readFrom}&to=${readTo}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Reading[]>
      })
      .then((d) => {
        if (!отменено) setReadings(d)
      })
      .catch((e) => {
        if (!отменено) setReadingsError(errorMessage(e))
      })
    // sectionId нарочно не в списке зависимостей, хотя используется внутри —
    // ниже почему. sectionId читается из ЭТОГО рендера и всегда свежий: эффект
    // перезапускается вместе с readFrom/readTo, а те меняются на КАЖДУЮ смену
    // участка (эффект выше синхронно сбрасывает их в '' и пересчитывает заново).
    //
    // Если добавить sectionId в зависимости, эффект стреляет ПРЕЖДЕ, чем даты
    // успевают сброситься: на смену участка первым срабатывает первый эффект
    // (его единственная зависимость — sectionId) и планирует readFrom/readTo → '',
    // но ЭТОТ эффект в том же проходе ещё видит СТАРЫЕ даты старого участка —
    // они не менялись, а sectionId в его собственном списке зависимостей уже
    // сменился, и он запускает fetch НОВОГО участка со СТАРЫМ окном дат. Запрос
    // настоящий и часто успевает раньше основного /api/objects/{id}: setReadings
    // получает реальный, непустой массив ДО того, как readFrom/readTo обнулились
    // на экране, и следующий кадр рисует его поверх пустого окна — те же NaN
    // <rect>, что чинит setReadings(null) выше, только с другой стороны.
    // Нашла ab, 22.09.2026, на переходе 2204 → 2157.
    return () => {
      отменено = true
    }
  }, [readFrom, readTo])

  if (notFound) {
    return (
      <main class="p-5">
        <p style="color:var(--text-muted)">
          Участок {sectionId} не найден или вне вашей области видимости.
        </p>
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
          {имяУчастка(data.smvu_key)}
        </h1>
        <p style="color:var(--text-secondary)">
          Участок <span class="num">{data.section_id}</span>, ключ СМВУ{' '}
          <code class="num">{data.smvu_key}</code>
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
              {['Тег', 'Название', 'Тип датчика'].map((h) => (
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
            {groupChannelsBySystem(data.channels).map((g) => (
              <>
                <tr key={`system-${g.systemKind}`}>
                  <td
                    colSpan={3}
                    class="px-2 py-2 text-xs font-semibold"
                    style="background:var(--bg-surface); border-bottom:1px solid var(--border-subtle)"
                  >
                    <SystemShape kind={g.systemKind} />
                    {g.systemKind}{' '}
                    <span class="font-normal num" style="color:var(--text-muted)">
                      · {g.channels.length}
                    </span>
                  </td>
                </tr>
                {g.channels.map((c) => (
                  <tr key={c.channel_id} style="border-bottom:1px solid var(--border-subtle)">
                    <td class="px-2 py-2 num">{c.tag}</td>
                    <td class="px-2 py-2">{c.name}</td>
                    <td class="px-2 py-2">{c.sensor_kind}</td>
                  </tr>
                ))}
              </>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
          Отказы по каналам
        </h2>
        {channelFaultsError && (
          <p style="color:var(--state-error)">
            Не удалось загрузить отказы по каналам: {channelFaultsError}
          </p>
        )}
        {channelFaults === null && !channelFaultsError && (
          <p style="color:var(--text-muted)">Загрузка…</p>
        )}
        {channelFaults && (
          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Датчик', 'Канал', 'Отказов', 'Последний', 'В среднем лежит'].map((h) => (
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
              {channelFaults.map((c) => (
                <tr key={c.channel_id} style="border-bottom:1px solid var(--border-subtle)">
                  <td class="px-2 py-2">{c.sensor_kind}</td>
                  <td class="px-2 py-2">{c.name}</td>
                  <td class="px-2 py-2 num">{c.faults_cnt}</td>
                  <td class="px-2 py-2 num">
                    {c.last_fault_at ? new Date(c.last_fault_at).toLocaleDateString('ru-RU') : '—'}
                  </td>
                  <td class="px-2 py-2 num">
                    {c.faults_cnt === 0 || c.avg_duration_h == null
                      ? 'в строю'
                      : `${c.avg_duration_h.toFixed(1).replace('.', ',')} ч`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {risk && (
        <section>
          <h2 class="text-sm font-semibold mb-2" style="color:var(--text-muted)">
            Уровень риска
          </h2>
          <div class="text-sm flex flex-col gap-1">
            <div>
              {DIRECTION_LABEL[risk.direction]}: вероятность{' '}
              <b class="num">{risk.probability.toFixed(4)}</b>, ранг{' '}
              <b class="num">{risk.risk_rank}</b>, горизонт {risk.horizon_h} ч
              {risk.is_stale && <span style="color:var(--state-warning)"> · устарело</span>}
            </div>
            {/* Две даты и две подписи — MOS-129. Раньше здесь стояла одна строка
                «Данные по состоянию на … — момент среза выгрузки заказчика»,
                и она врала дважды: `as_of` это срез ПРОГОНА, а не свойство
                выгрузки, и к данным именно этого участка он отношения не имеет.
                Участок 2477 показывал сверху 19.09.2026, а его последняя запись
                — 22.04.2026, разрыв 150 суток, и объяснение модели в этой же
                карточке говорило «датчики молчат 150 суток подряд». Три
                утверждения на одном экране, и неверным было ровно это.

                Имя «последняя запись участка» взято у соседнего блока «Показания
                датчиков», а не придумано новое («край данных по участку», как
                названо в тикете): одно число обязано на экране называться одним
                словом, иначе диспетчер читает две подписи как два разных факта —
                ровно та беда, ради которой этот тикет и заведён. */}
            <div style="color:var(--text-secondary)">
              Считали на срез {new Date(risk.as_of).toLocaleDateString('ru-RU')} — не время расчёта
              и не последняя запись по этому участку
            </div>
            <div style="color:var(--text-secondary)">
              {data.last_reading_at
                ? `Последняя запись этого участка — ${new Date(data.last_reading_at).toLocaleDateString('ru-RU')}: позже неё датчики участка не писали ничего`
                : 'Последней записи у этого участка нет вовсе — датчики не писали ни разу'}
            </div>
          </div>

          {explanationLines.length > 0 && (
            <div
              class="text-sm p-3 mt-2 rounded"
              style="background:var(--bg-surface); border-left:3px solid var(--brand)"
            >
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
            {groupRepeatedForecasts(data.recent_forecasts).map(({ forecast: f, repeats }) => (
              <tr
                key={f.forecast_id}
                {...rowLink(() => route(`/forecasts/${f.forecast_id}`))}
                style="border-bottom:1px solid var(--border-subtle); cursor:pointer"
              >
                <td class="px-2 py-2 num">
                  {new Date(f.computed_at).toLocaleString('ru-RU')}
                  {repeats > 1 && <span style="color:var(--text-muted)"> · {repeats}×</span>}
                </td>
                <td class="px-2 py-2">{DIRECTION_LABEL[f.direction]}</td>
                <td class="px-2 py-2 num">{f.probability.toFixed(4)}</td>
                <td class="px-2 py-2 num">{f.risk_rank}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.recent_forecasts.length === 0 && (
          <p style="color:var(--text-muted)">Прогнозов по участку нет.</p>
        )}
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
        <div
          class="flex flex-wrap items-end gap-4 text-sm mb-3"
          style="color:var(--text-secondary)"
        >
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
                  {chReadings.length < 2 ? (
                    <p class="text-sm" style="color:var(--text-muted)">
                      {chReadings.length === 0
                        ? 'Нет показаний за период.'
                        : `За окно ${shortDate(readFrom)}–${shortDate(readTo)} у канала 1 запись.`}
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

// Фигура для подзаголовка группы каналов (MOS-172) — шесть значений
// smvu.channel.system_kind (db/migrations/004_events.sql:38-44), седьмая
// (NO_SYSTEM_KIND) — без фигуры: пустая заливка соврала бы, будто у канала
// есть система. Цвета нет ни у одной фигуры (currentColor), четыре залиты
// сплошняком, две нарисованы обводкой — читается на чёрно-белой распечатке
// той же логикой, что НФ-50 для состояний.
//
// Какая фигура какой системе — назначили мы. Легенды заказчика у нас нет,
// в выгрузке обозначений систем нет вовсе; на защите это наше решение,
// а не срисованное у СМВУ.
const SYSTEM_SHAPE: Record<
  string,
  'triangle' | 'square' | 'circle' | 'diamond' | 'square-outline' | 'cross'
> = {
  'Пожарная охрана': 'triangle',
  'Диспетчерский контроль': 'square',
  'Охранная подсистема': 'circle',
  'Температурная подсистема': 'diamond',
  // Был восьмиугольник — на 12×12 он расходится с кругом на 2 px из 144, на
  // чёрно-белой распечатке неотличим (нашёл 5f, 22.09.2026, замером расстояния
  // между фигурами). Полый квадрат — следующий по непохожести на уже занятые
  // формы, до ближайшего соседа (круга) 50 px.
  'Газовая охрана': 'square-outline',
  'Диагностическая подсистема': 'cross',
}

function SystemShape({ kind }: { kind: string }) {
  const shape = SYSTEM_SHAPE[kind]
  if (!shape) return null
  return (
    <svg
      viewBox="0 0 16 16"
      width="12"
      height="12"
      class="inline-block align-middle mr-1"
      fill="currentColor"
    >
      {shape === 'triangle' && <polygon points="8,1 15,15 1,15" />}
      {shape === 'square' && <rect x="2" y="2" width="12" height="12" />}
      {shape === 'circle' && <circle cx="8" cy="8" r="7" />}
      {shape === 'diamond' && <polygon points="8,1 15,8 8,15 1,8" />}
      {shape === 'square-outline' && (
        <rect
          x="2.5"
          y="2.5"
          width="11"
          height="11"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        />
      )}
      {shape === 'cross' && (
        <>
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5" />
          <path d="M5,8 H11 M8,5 V11" stroke="currentColor" stroke-width="1.5" />
        </>
      )}
    </svg>
  )
}

// Ось времени под лентой/линией — 4 деления (начало, конец и две между ними),
// подпись через formatDateTime — общий формат дат по всему фронту (М-12).
function TimeAxis({ start, end }: { start: number; end: number }) {
  const W = 1000
  const TICKS = 4
  const ticks = axisTicks(start, end, TICKS)
  return (
    <svg viewBox={`0 0 ${W} 14`} class="w-full">
      {ticks.map((t, i) => (
        <text
          key={i}
          x={i === 0 ? 0 : i === TICKS - 1 ? W : (W * i) / (TICKS - 1)}
          y="11"
          font-size="10"
          fill="var(--text-muted)"
          text-anchor={i === 0 ? 'start' : i === TICKS - 1 ? 'end' : 'middle'}
        >
          {formatDateTime(new Date(t).toISOString())}
        </text>
      ))}
    </svg>
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
    <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        class="w-full"
        style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px"
      >
        <path
          d={d}
          fill="none"
          stroke="var(--chart-outline)"
          stroke-width="4"
          stroke-linecap="round"
        />
        <path d={d} fill="none" stroke="var(--chart-6)" stroke-width="2" stroke-linecap="round" />
        <text x={PAD} y={PAD + 2} font-size="10" fill="var(--text-muted)">
          {fmtValue(vMax)}
        </text>
        <text x={PAD} y={H - 3} font-size="10" fill="var(--text-muted)">
          {fmtValue(vMin)}
        </text>
      </svg>
      <TimeAxis start={tMin} end={tMax} />
    </>
  )
}

// Нечисловой ряд — лента состояний: сегмент от одной записи до следующей,
// цвет по is_alarm (это настройка прибора, не решение человека — поэтому
// цвет служебный var(--state-warning), а не шкала риска и не слово "авария").
function StateRibbon({ readings, from, to }: { readings: Reading[]; from: string; to: string }) {
  const W = 1000
  const H = 36
  // new Date("2026-06-24") — полночь UTC, то есть 03:00 по Москве (нашла ab,
  // 22.09.2026): граница ленты уезжала на три часа от дат в полях выше, и часть
  // показаний оказывалась левее начала оси. Россия не переходит на летнее время
  // с 2014 года, смещение фиксированное — +03:00 можно не вычислять, а написать.
  const winStart = new Date(`${from}T00:00:00+03:00`).getTime()
  const winEnd = new Date(`${to}T00:00:00+03:00`).getTime() + 24 * 3600 * 1000
  // На стыке смены участка эффекты выше на один кадр могут прислать пустые
  // from/to при уже непустых readings (нашла ab, 22.09.2026, дважды поймала
  // вживую даже после починки зависимостей). new Date('') даёт NaN, а
  // TimeAxis зовёт .toISOString() на нём и бросает исключение, роняя весь
  // блок «Показания датчиков» без самовосстановления. Гвард закрывает класс
  // целиком: любая будущая гонка того же вида станет одним пустым кадром,
  // а не сломанным экраном.
  if (!Number.isFinite(winStart) || !Number.isFinite(winEnd)) return null
  const x = (t: number) => ((t - winStart) / (winEnd - winStart || 1)) * W

  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        class="w-full"
        style="background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:4px"
      >
        {readings.map((r, i) => {
          const t0 = new Date(r.read_time).getTime()
          const t1 =
            i + 1 < readings.length ? new Date(readings[i + 1].read_time).getTime() : winEnd
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
      <TimeAxis start={winStart} end={winEnd} />
    </>
  )
}
