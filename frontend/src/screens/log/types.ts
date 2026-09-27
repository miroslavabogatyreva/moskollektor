export { type Direction, DIRECTION_LABEL } from '../../lib/direction'
import type { Direction } from '../../lib/direction'
import type { Decision } from '../../components/VerdictDialog'
import type { Outcome } from '../../components/OutcomeDialog'

export interface ForecastRow {
  forecast_id: number
  section_id: number
  computed_at: string // ISO, pred.run.started_at
  direction: Direction
  probability: number
  horizon_h: number
  // Последнее решение диспетчера (US-08, US-09 сц. 4); null — не разобран.
  decision: Decision | null
  // Исход (US-10); null — не отмечен, тогда horizon_expired решает слова в журнале.
  outcome: Outcome | null
  horizon_expired: boolean
}

export interface ForecastListResponse {
  total: number
  items: ForecastRow[]
}
