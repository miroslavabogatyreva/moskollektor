import { riskColors } from '../screens/map/risk'
import type { SensorLevel } from '../lib/sensorRisk'

// Три уровня датчика (эпик MOS-248) — одни цвета и слова на схеме (SensorDemo.tsx)
// и на дашборде. high и normal — те же токены, что у оси (risk.ts); для «наблюдать»
// берём --risk-medium, который tokens.css держит под трёхуровневую шкалу.
export const SENSOR_LEVELS: Record<
  SensorLevel,
  { label: string; fill: string; text: string; border: string }
> = {
  high: { label: 'высокий риск', ...riskColors('high') },
  watch: {
    label: 'наблюдать',
    fill: 'var(--risk-medium)',
    text: 'var(--risk-medium-text)',
    border: 'var(--risk-medium-border)',
  },
  normal: { label: 'норма', ...riskColors('normal') },
}

export function SensorBadge({ level }: { level: SensorLevel }) {
  const c = SENSOR_LEVELS[level]
  return (
    <span
      data-level={level}
      class="inline-block rounded-full text-xs whitespace-nowrap"
      style={`padding:1px 8px; background:${c.fill}; color:${c.text}; border:1px solid ${c.border}`}
    >
      {c.label}
    </span>
  )
}
