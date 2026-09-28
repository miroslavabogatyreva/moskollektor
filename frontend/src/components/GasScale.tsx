import { formatDateTime } from '../lib/format'
import { fmtValue } from './ObjectCard.logic'
import {
  меткаМетана,
  позиция,
  последнееЧисло,
  ШКАЛА_МЕТАНА,
  УСТАВКИ_МЕТАНА,
  type Зона,
} from './GasScale.logic'

/* Загазованность участка (MOS-169, приёмка Ф-30): на каждый газовый канал —
   шкала 0–2 % об. метана, две подписанные отметки уставок 0,75 и 1,5 % об.
   и метка последнего числа за окно «Показаний датчиков» (тот же ответ
   GET /api/objects/{id}/readings, второго запроса нет). Кислородной шкалы нет:
   кислородных каналов в выгрузке 0. Арифметика — GasScale.logic.ts. */

export const ГАЗОВЫЙ_ДАТЧИК = 'Газовый датчик'

const ЗОНА: Record<Зона, { text: string; color: string }> = {
  below: { text: 'ниже первой уставки', color: 'var(--risk-low-text)' },
  between: { text: 'между первой и второй уставкой', color: 'var(--risk-high)' },
  above: { text: 'на второй уставке и выше', color: 'var(--risk-critical)' },
  none: { text: 'числовых показаний за окно нет', color: 'var(--risk-nodata-text)' },
}

// Полосы ступеней под шкалой: до первой уставки, между ними, после второй.
const [У1, У2] = УСТАВКИ_МЕТАНА
const ПОЛОСЫ = [
  { from: 0, to: позиция(У1), fill: 'var(--risk-low)' },
  { from: позиция(У1), to: позиция(У2), fill: 'var(--risk-medium)' },
  { from: позиция(У2), to: 100, fill: 'var(--risk-critical)' },
]

interface Channel {
  channel_id: number
  name: string
}
interface Reading {
  read_time: string
  channel_id: number
  value_num: number | null
}

export function GasScale({
  channels,
  readings,
  readingsError,
}: {
  channels: Channel[]
  readings: Reading[] | null
  readingsError: string | null
}) {
  return (
    <section data-testid="gas-scales" class="card">
      <h2 class="card-title mb-1">Загазованность</h2>
      <p class="text-sm mb-2" style="color:var(--text-secondary)">
        Метан, % об. Уставки 0,75 и 1,5 % об.; метка — последнее показание в окне «Показаний
        датчиков» ниже.
      </p>
      {readingsError && (
        <p style="color:var(--state-error)">Не удалось загрузить показания: {readingsError}</p>
      )}
      {readings === null && !readingsError && <p style="color:var(--text-muted)">Загрузка…</p>}
      {readings && (
        <div class="flex flex-col gap-5">
          {channels.map((c) => {
            const last = последнееЧисло(readings.filter((r) => r.channel_id === c.channel_id))
            return (
              <Scale
                key={c.channel_id}
                channel={c}
                value={last?.value_num ?? null}
                at={last?.read_time}
              />
            )
          })}
        </div>
      )}
    </section>
  )
}

function Scale({ channel, value, at }: { channel: Channel; value: number | null; at?: string }) {
  const м = меткаМетана(value)
  const [lo, hi] = ШКАЛА_МЕТАНА
  return (
    <div data-gas-scale={channel.channel_id} data-zone={м.zone} class="max-w-xl">
      <div class="text-sm font-semibold mb-1">{channel.name}</div>
      <div class="text-sm mb-6" style={`color:${ЗОНА[м.zone].color}`}>
        {value != null && <span class="num font-semibold">{fmtValue(value)} % об. · </span>}
        {ЗОНА[м.zone].text}
        {м.outOfScale && ' · за пределами шкалы'}
        {at && (
          <span class="num" style="color:var(--text-muted)">
            {' '}
            · {formatDateTime(at)}
          </span>
        )}
      </div>
      <div
        role="meter"
        aria-label={`Метан, ${channel.name}`}
        aria-valuemin={lo}
        aria-valuemax={hi}
        aria-valuenow={value ?? undefined}
        aria-valuetext={
          value != null ? `${fmtValue(value)} % об., ${ЗОНА[м.zone].text}` : ЗОНА.none.text
        }
        class="relative h-3 rounded-sm"
        style="border:1px solid var(--border-strong)"
      >
        {ПОЛОСЫ.map((p) => (
          <div
            key={p.from}
            class="absolute top-0 bottom-0"
            style={`left:${p.from}%; width:${p.to - p.from}%; background:${p.fill}`}
          />
        ))}
        {УСТАВКИ_МЕТАНА.map((v) => (
          <div
            key={v}
            data-setpoint={v}
            class="absolute"
            style={`left:${позиция(v)}%; top:-20px; bottom:-4px; border-left:2px solid var(--text-primary)`}
          >
            <span
              class="absolute text-xs num whitespace-nowrap"
              style="top:0; left:4px; color:var(--text-secondary); line-height:1"
            >
              {String(v).replace('.', ',')} % об.
            </span>
          </div>
        ))}
        {м.pct != null && (
          <div
            data-value-marker={value}
            class="absolute"
            style={`left:${м.pct}%; top:-4px; bottom:-4px; width:0; border-left:4px solid ${ЗОНА[м.zone].color}; transform:translateX(-2px)`}
          />
        )}
      </div>
      <div class="flex justify-between text-xs num mt-1" style="color:var(--text-muted)">
        <span>{fmtValue(lo)}</span>
        <span>{fmtValue(hi)} % об.</span>
      </div>
    </div>
  )
}
