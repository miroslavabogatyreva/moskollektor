export interface RiskRow {
  section_id: number
  probability: number
  risk_rank: number
  horizon_h: number
  as_of: string // ISO, срез данных: момент, на который посчитаны признаки, а не время расчёта
  is_stale: boolean // true — отдан прошлый результат, сегодняшний расчёт по объекту не прошёл
}

// GET /api/data-status (MOS-148). Три момента, которые на экране легко спутать.
export interface DataStatus {
  data_edge: string | null // докуда доехала выгрузка заказчика; null — суточная свёртка пуста
  as_of: string | null // срез, на котором считал последний прогон
  computed_at: string | null // когда этот прогон отработал
  // На сколько суток срез отстал от края. Считает сервер, а не браузер: вычитание
  // двух дат здесь пошло бы в поясе браузера. Показывает это поле MOS-129, здесь
  // оно описано, чтобы тип не расходился с ответом метода.
  lag_days: number | null
}
