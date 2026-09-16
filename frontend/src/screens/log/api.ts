import type { ForecastRow } from './types'

export interface ForecastQuery {
  from?: string
  to?: string
}

/* GET /api/forecasts?from=&to= — М-16, Ф-55, задача Q4 ещё не сделана.
   Пока отвечаем пустым списком, как ответит сам API до первого расчёта:
   экран обязан показать «прогнозов за период нет», а не зависнуть спиннером. */
export async function fetchForecasts(_query: ForecastQuery): Promise<ForecastRow[]> {
  return []
}
