// Чистая арифметика карточки объекта (MOS-131, MOS-52), вынесена из ObjectCard.tsx
// отдельным файлом без JSX — тем же приёмом, что screens/map/viewport.ts, —
// чтобы ObjectCard.selfcheck.ts мог импортировать её напрямую через node
// (Node вырезает типы из .ts, но не умеет JSX из .tsx).
import type { Direction } from '../lib/direction'

export interface RecentForecast {
  forecast_id: number
  direction: Direction
  probability: number
  risk_rank: number
  explanation_ru: string | null
  computed_at: string
}

// toISOString() режет сутки по UTC, а last_reading_at и подписи на экране —
// по Москве (нашла ab, 22.09.2026): у записи 21:55 UTC московская дата уже
// на день больше, окно теряло последние сутки ровно там, где должно было
// заканчиваться. У 53 участков из 3166 это меняло дату, у остальных — нет.
export function isoDate(d: Date): string {
  return d.toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' })
}

// "2026-04-16" → "16.04" — короче formatDateTime: тут это подпись к фразе
// про пустое окно, а не строка в таблице, год и время в ней лишние.
export function shortDate(iso: string): string {
  const [, m, d] = iso.split('-')
  return `${d}.${m}`
}

// Окно по умолчанию — 7 суток, оканчивающихся последней записью участка
// (last_reading_at из GET /api/objects/{id}), а не сегодняшней датой: у 54,4%
// участков за последние 7 суток выгрузки нет ни строки, данные могли замолчать
// задолго до конца выгрузки. Без last_reading_at (участок совсем без записей —
// сегодня таких нет) откатываемся на 7 суток от сегодня.
export function defaultWindow(lastReadingAt: string | null): [string, string] {
  const end = lastReadingAt ? new Date(lastReadingAt) : new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 6)
  return [isoDate(start), isoDate(end)]
}

// Модель прогоняется каждый час и часто пишет тот же прогноз заново (то же
// направление, вероятность, ранг и текст объяснения) — список из 10 записей
// превращается в одну строку, повторенную десять раз. Список уже отсортирован
// по убыванию времени (ORDER BY r.started_at DESC в GET /api/objects/{id}),
// поэтому повторы всегда идут подряд, и схлопывать можно соседей.
export function groupRepeatedForecasts(
  items: RecentForecast[],
): { forecast: RecentForecast; repeats: number }[] {
  const groups: { forecast: RecentForecast; repeats: number }[] = []
  for (const f of items) {
    const last = groups[groups.length - 1]
    if (
      last &&
      last.forecast.direction === f.direction &&
      last.forecast.probability === f.probability &&
      last.forecast.risk_rank === f.risk_rank &&
      last.forecast.explanation_ru === f.explanation_ru
    ) {
      last.repeats++
    } else {
      groups.push({ forecast: f, repeats: 1 })
    }
  }
  return groups
}

// Единицы измерения у value_num в данных нет (ни в smvu.reading, ни в ответе
// API) — сигнал разный от прибора к прибору, и без справочника заказчика её
// не восстановить. Подписываем голым числом, а не выдумываем "°C" или "В".
export function fmtValue(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace('.', ',')
}

// Деления оси времени под лентой/линией — n меток от start до end включительно,
// равномерно по времени (мс). n=4 даёт начало, конец и две между ними.
export function axisTicks(start: number, end: number, n = 4): number[] {
  return Array.from({ length: n }, (_, i) => start + ((end - start) * i) / (n - 1))
}

// Все 1 142 канала без system_kind — заглушки с section_id = NULL
// (db/migrations/004_events.sql:120-125), обе выборки этого экрана фильтруют
// по участку, и на карточку заглушка попасть не может — проверено 200 живыми
// участками, ни одного пустого. Метку всё равно держим: поле в базе честно
// nullable, а `||` вместо `??` бесплатно ловит и пустую строку заодно с null.
export const NO_SYSTEM_KIND = 'Без системы'

// Порядок групп — по первому появлению в списке каналов (тот уже отсортирован
// по channel_id), не по выдуманному справочнику приоритетов систем.
export function groupChannelsBySystem<C extends { system_kind: string | null }>(
  channels: C[],
): { systemKind: string; channels: C[] }[] {
  const groups: { systemKind: string; channels: C[] }[] = []
  const byKind = new Map<string, { systemKind: string; channels: C[] }>()
  for (const c of channels) {
    const kind = c.system_kind || NO_SYSTEM_KIND
    let group = byKind.get(kind)
    if (!group) {
      group = { systemKind: kind, channels: [] }
      byKind.set(kind, group)
      groups.push(group)
    }
    group.channels.push(c)
  }
  return groups
}

// Эпизод потери связи на графике (US-07 сц. 3) — подряд идущие записи
// «Неисправен» канала, дольше часа, как в определении отказа (docs/for-ml-team.md:
// эпизод `Неисправен` длиннее часа). Начало — сама запись журнала СМВУ, с которой
// эпизод открылся, поэтому его время на графике совпадает с журналом до секунды.
// Конец — первая запись с другим значением; не пришла — эпизод открыт до конца окна.
export interface ЭпизодПотери {
  start: string
  startMs: number
  endMs: number
}

const ЧАС_МС = 3600 * 1000

export function эпизодыПотериСвязи(
  readings: { read_time: string; value_text: string | null }[],
  winEndMs: number,
): ЭпизодПотери[] {
  const out: ЭпизодПотери[] = []
  let открыт: { start: string; startMs: number } | null = null
  const закрыть = (endMs: number) => {
    if (открыт && endMs - открыт.startMs > ЧАС_МС) out.push({ ...открыт, endMs })
    открыт = null
  }
  for (const r of readings) {
    const t = Date.parse(r.read_time)
    if (r.value_text === 'Неисправен') {
      if (!открыт) открыт = { start: r.read_time, startMs: t }
    } else if (открыт) закрыть(t)
  }
  закрыть(winEndMs)
  return out
}
