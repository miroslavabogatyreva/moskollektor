import type { ComponentChildren } from 'preact'
import { riskColors, type RiskClass } from '../screens/map/risk'

// Значок класса риска словом и иконкой — один на схему и дашборд: тот же цвет
// и та же иконка в легенде схемы и в столбце «Риск» таблицы. Иконки — контуры
// Tabler Icons (MIT), три пути вписаны сюда, а не webfont: шрифт на 5 000 иконок
// ради трёх стоил бы сотни килобайт (бюджет и история Lexend — tokens.css).
const ICONS: Record<'high' | 'normal' | 'none', string[]> = {
  // ti-alert-triangle
  high: [
    'M12 9v4',
    'M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0z',
    'M12 16h.01',
  ],
  // ti-shield-check
  normal: [
    'M11.46 20.846a12 12 0 0 1 -7.96 -14.846a12 12 0 0 0 8.5 -3a12 12 0 0 0 8.5 3a12 12 0 0 1 -.09 7.06',
    'M15 19l2 2l4 -4',
  ],
  // ti-minus-vertical — та же вертикальная черта, что значок «класса нет» на оси.
  // Не ti-circle-dashed: на ч/б распечатке при 16 px пунктирный круг не отличить
  // от щита (US-05 сц. 5 намерил 0 px разницы), а пунктир в палитре заказчика
  // занят под «нет связи с каналом» (dashboard/color-palette.md, разд. 5.3).
  none: ['M12 5v14'],
}

export function RiskBadge({ cls, children }: { cls: RiskClass; children: ComponentChildren }) {
  const c = riskColors(cls)
  // «Класса нет» — без заливки цветом: светлый фон, рамка и приглушённый текст.
  const style =
    cls === 'high' || cls === 'normal'
      ? `background:${c.fill}; color:${c.text}; border:1px solid ${c.border}`
      : 'background:var(--bg-table); color:var(--text-muted); border:1px solid var(--border-subtle)'
  return (
    <span
      class="inline-flex items-center gap-1.5 rounded-full text-sm whitespace-nowrap"
      style={`padding:2px 10px; ${style}`}
    >
      <svg
        aria-hidden="true"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        {ICONS[cls ?? 'none'].map((d) => (
          <path key={d} d={d} />
        ))}
      </svg>
      {children}
    </span>
  )
}
