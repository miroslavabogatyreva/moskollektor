export { type Direction, DIRECTION_LABEL } from '../../lib/direction'
import type { Direction } from '../../lib/direction'
import type { Decision } from '../../components/VerdictDialog'

export interface ForecastRow {
  forecast_id: number
  section_id: number
  computed_at: string // ISO, pred.run.started_at
  direction: Direction
  probability: number
  horizon_h: number
  // Последнее решение диспетчера (US-08, US-09 сц. 4); null — не разобран.
  decision: Decision | null
}

export interface ForecastListResponse {
  total: number
  items: ForecastRow[]
}
