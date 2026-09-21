export interface RiskRow {
  section_id: number
  probability: number
  risk_rank: number
  horizon_h: number
  as_of: string // ISO, срез данных: момент, на который посчитаны признаки, а не время расчёта
  is_stale: boolean // true — отдан прошлый результат, сегодняшний расчёт по объекту не прошёл
}
