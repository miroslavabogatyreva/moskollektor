export { type Direction, DIRECTION_LABEL } from '../../lib/direction'
import type { Direction } from '../../lib/direction'

export interface ForecastRow {
  forecast_id: number
  section_id: number
  computed_at: string // ISO, pred.run.started_at
  direction: Direction
  probability: number
  horizon_h: number
}
