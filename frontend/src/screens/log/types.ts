export type Direction = 'sensor_failure' | 'fire' | 'unauthorized_access' | 'wear'

export interface ForecastRow {
  forecast_id: number
  section_id: number
  computed_at: string // ISO, pred.run.started_at
  direction: Direction
  probability: number
  horizon_h: number
}

// Коды из CHECK-ограничения pred.forecast.direction (db/migrations/004_events.sql).
// docs/HLD.md разд. 5.3 в одном месте вместо "wear" пишет "flooding" — это несовпадение
// с реальной схемой, не наша ошибка; берём то, что в CHECK.
export const DIRECTION_LABEL: Record<Direction, string> = {
  sensor_failure: 'Отказ датчика',
  fire: 'Пожар',
  unauthorized_access: 'Несанкционированный доступ',
  wear: 'Износ',
}
