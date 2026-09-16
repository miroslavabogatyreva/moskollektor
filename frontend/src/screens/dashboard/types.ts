export interface RiskRow {
  section_id: number
  probability: number
  risk_rank: number
  horizon_h: number
  computed_at: string // ISO, момент среза данных, а не время расчёта
  is_stale: boolean // true — отдан прошлый результат, сегодняшний расчёт по объекту не прошёл
}
