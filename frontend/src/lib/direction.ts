export type Direction = 'sensor_failure' | 'fire' | 'unauthorized_access' | 'wear'

// Коды из CHECK-ограничения pred.forecast.direction (db/migrations/004_events.sql).
// docs/HLD.md разд. 5.3 в одном месте вместо "wear" пишет "flooding" — это несовпадение
// с реальной схемой, не наша ошибка; берём то, что в CHECK.
export const DIRECTION_LABEL: Record<Direction, string> = {
  sensor_failure: 'Отказ датчика',
  fire: 'Пожар',
  unauthorized_access: 'Несанкционированный доступ',
  wear: 'Износ',
}
