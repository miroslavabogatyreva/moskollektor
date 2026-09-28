// Прогноз по датчикам — эпик MOS-248: SL.5 (дашборд, MOS-254) и SL.6 (схема, MOS-255).
// Типы ответа GET /api/sensor-risk и GET /api/sensor-risk/summary (SL.4, MOS-253)
// и чистая логика без DOM, общая для двух экранов. Проверяется node-скриптом
// sensorRisk.selfcheck.ts, как rows.ts и risk.ts.

export type SensorLevel = 'high' | 'watch' | 'normal'

export interface SensorReason {
  text: string
  weight: number
  // real — из журнала СМВУ, synthetic — из выдуманного паспорта, plan — окно графика ППР.
  kind: 'real' | 'synthetic' | 'plan'
}

export interface SensorEquipment {
  equipment_no: string
  manufacturer: string
  model_no: string
  in_service_from: string
  service_life_years: number
  last_check_at: string | null
  last_check_ok: boolean | null
}

export interface SensorRow {
  channel_id: number
  name: string
  sensor_kind: string
  // Ответ SL.4 (backend/app/api/schemas.py, SensorRiskItem). Имени коллектора
  // в строке нет — экран берёт его из /data/sections.json по collector_id.
  node_id: number | null
  collector_id: number | null
  // null у каналов без «ПК» в названии: охранная зона, здание ДП — 765 из 11 485 (миграция 031).
  picket: number | null
  section_id: number | null
  score: number
  level: SensorLevel
  reasons: SensorReason[]
  // null при synthetic=0: паспорт синтетический, без него паспорта нет вовсе.
  equipment: SensorEquipment | null
}

// as_of null — worker ещё не посчитал ни одного среза pred.sensor_risk.
export interface SensorRiskPage {
  synthetic: boolean
  as_of: string | null
  node: number | null
  node_name: string | null
  collector: number | null
  collector_name: string | null
  total: number
  limit: number
  offset: number
  items: SensorRow[]
}

export interface SensorSummary {
  synthetic: boolean
  as_of: string | null
  high: number
  watch: number
  normal: number
  collectors_with_high: number
  // Сервер отдаёт не больше пяти, остальные — collectors_with_high минус длина списка.
  top_collectors: { collector_id: number; name: string; high: number }[]
}

// «расчёт на 30.06.2026 23:59» или «расчёта ещё не было», если срезов нет.
export const срезРасчёта = (as_of: string | null, fmt: (v: string) => string): string =>
  as_of ? `расчёт на ${fmt(as_of)}` : 'расчёта ещё не было'

// Переключатель «Учитывать паспорт оборудования (синтетика)»: по умолчанию включён,
// в адресе живёт только выключенное состояние — ?synthetic=0. Так ссылка без
// параметра (меню, письмо заказчику) открывает демо целиком.
export const синтетикаВключена = (param: unknown): boolean => param !== '0'

// Фильтр уровня таблицы на дашборде живёт в адресе: ?level=high|watch|normal (MOS-262).
// Чужое значение — как «все»: ссылка с опечаткой не должна показать пустую таблицу.
export const уровеньИзАдреса = (param: unknown): SensorLevel | '' =>
  param === 'high' || param === 'watch' || param === 'normal' ? param : ''

export function sensorRiskUrl(q: {
  synthetic: boolean
  collector?: number
  node?: number
  // Один датчик — чтобы /map?channel=<id> узнал коллектор датчика. В контракте
  // SL.4 параметра нет, просим добавить (PR sl-frontend); ответ проверяем по channel_id.
  channel?: number
  level?: SensorLevel
  limit?: number
  offset?: number
}): string {
  const p = new URLSearchParams({ synthetic: q.synthetic ? '1' : '0' })
  if (q.collector != null) p.set('collector', String(q.collector))
  if (q.node != null) p.set('node', String(q.node))
  if (q.channel != null) p.set('channel', String(q.channel))
  if (q.level) p.set('level', q.level)
  if (q.limit != null) p.set('limit', String(q.limit))
  if (q.offset) p.set('offset', String(q.offset))
  return `/api/sensor-risk?${p}`
}

// Адрес с одним изменённым параметром, остальные — как были: переключатель
// синтетики не должен терять ?channel= на схеме или ?view= на дашборде.
// value null — параметр убрать.
export function сПараметром(
  path: string,
  search: string,
  key: string,
  value: string | null,
): string {
  const p = new URLSearchParams(search)
  if (value == null) p.delete(key)
  else p.set(key, value)
  const s = p.toString()
  return s ? `${path}?${s}` : path
}

// Главная причина — самая весомая из тех, что прибавили к баллу. Плановая (окно
// ППР) веса не прибавляет, а объясняет: берём её, только когда других нет.
export function главнаяПричина(reasons: SensorReason[]): SensorReason | null {
  const весомые = reasons.filter((r) => r.kind !== 'plan')
  if (весомые.length === 0) return reasons[0] ?? null
  return весомые.reduce((a, b) => (b.weight > a.weight ? b : a))
}

const число = new Intl.NumberFormat('ru-RU')

// «1–50 из 11 485» под таблицей. Пустая выборка — «0 из 0», а не «1–0».
export function страница(offset: number, count: number, total: number): string {
  if (count === 0) return `0 из ${число.format(total)}`
  return `${число.format(offset + 1)}–${число.format(offset + count)} из ${число.format(total)}`
}

// Линия схемы — префикс тега участка (smvu_key = «префикс:пикет»), как у оси
// AxisLine.tsx: у «объекта Мю» две линии, 914 и 915, и пикет 0 у каждой свой.
// Датчик без участка (section_id null) или с участком, которого нет в справочнике,
// идёт на линию «—», чтобы не пропасть со схемы молча.
export const БЕЗ_ЛИНИИ = '—'
export function линииДатчиков<T extends Pick<SensorRow, 'section_id'>>(
  items: T[],
  ключУчастка: Map<number, string>,
): [string, T[]][] {
  const m = new Map<string, T[]>()
  for (const s of items) {
    const key = s.section_id != null ? ключУчастка.get(s.section_id) : undefined
    const prefix = key ? key.split(':')[0] : БЕЗ_ЛИНИИ
    m.set(prefix, [...(m.get(prefix) ?? []), s])
  }
  return [...m.entries()].sort((a, b) =>
    a[0] === БЕЗ_ЛИНИИ ? 1 : b[0] === БЕЗ_ЛИНИИ ? -1 : Number(a[0]) - Number(b[0]),
  )
}
