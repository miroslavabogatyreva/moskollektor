import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../../lib/api'
import { errorMessage, formatDateTime } from '../../lib/format'

/* «Настройки» — US-23 (MOS-208), НФ-43, НФ-44. Администратор меняет пороги риска
   и автозаявки и горизонт прогноза без выкладки: GET /api/settings читает
   ref.app_setting, «Сохранить» шлёт PUT /api/settings/{key} по одной строке.
   Границы значений проверяет сервер (backend/app/api/settings.py, _RULES) — экран
   показывает его ответ 422 словами, а не дублирует правила. Старое и новое
   значение ложатся в журнал действий (details у audit.user_action). */

interface Setting {
  key: string
  value: number
  unit: string | null
  changed_at: string
}

// Подписи ключей из ref.app_setting и порядок строк на экране: сначала пороги,
// ради которых администратор сюда приходит (US-23). Ключ без подписи — в конце, как есть.
const ПОДПИСИ: Record<string, string> = {
  risk_threshold_high: 'Порог высокого риска',
  risk_class_hysteresis: 'Гистерезис класса риска',
  risk_class_hold_min: 'Удержание класса риска',
  order_threshold_a: 'Порог автозаявки, класс A',
  order_threshold_b: 'Порог автозаявки, класс B',
  order_threshold_c: 'Порог автозаявки, класс C',
  order_top_sections_per_object: 'Участков объекта в автозаявке',
  order_preventive_cap_h: 'Потолок срока профилактической заявки',
  forecast_horizon_h: 'Горизонт прогноза',
  forecast_heartbeat_min: 'Запись в журнал прогнозов не реже',
  forecast_deadband: 'Мёртвая зона записи в журнал прогнозов',
  forecast_spread_enabled: 'Разнос вероятности объекта по участкам (0 или 1)',
  forecast_weight_alpha: 'Сглаживание веса участка (alpha)',
  forecast_weight_window_from: 'Окно отказов для веса участка, с',
  forecast_weight_window_to: 'Окно отказов для веса участка, по',
  precision_min: 'Целевой Precision',
  recall_min: 'Целевой Recall',
}

const inputStyle =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function SettingsScreen(_props: Record<string, unknown>) {
  const [items, setItems] = useState<Setting[] | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch('/api/settings')
      .then(async (r) => {
        if (r.status === 403) return setForbidden(true)
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        const порядок = (k: string) => {
          const i = Object.keys(ПОДПИСИ).indexOf(k)
          return i < 0 ? Infinity : i
        }
        const все = (await r.json()) as Setting[]
        setItems(все.sort((a, b) => порядок(a.key) - порядок(b.key)))
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Настройки
      </h1>

      {forbidden && (
        <p style="color:var(--state-error)">Настройки доступны только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">Не удалось загрузить настройки: {error}</p>}
      {!items && !forbidden && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {items && (
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Настройка', 'Значение', 'Единица', 'Изменено', ''].map((h) => (
                <th
                  key={h}
                  class="text-left px-2 py-2 text-xs uppercase tracking-wide"
                  style="color:var(--text-muted); border-bottom:1px solid var(--border-subtle)"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <Строка key={s.key} s={s} />
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}

function Строка({ s: начальная }: { s: Setting }) {
  const [s, setS] = useState(начальная)
  const [value, setValue] = useState(String(начальная.value))
  const [итог, setИтог] = useState<{ ok: boolean; text: string } | null>(null)
  const подпись = ПОДПИСИ[s.key] ?? s.key

  async function сохранить(e: Event) {
    e.preventDefault()
    setИтог(null)
    try {
      // Строкой, а не числом: сервер читает Decimal, и 0.1 не превращается в 0.1000000001.
      const r = await apiFetch(`/api/settings/${s.key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: value.trim().replace(',', '.') }),
      })
      if (!r.ok) {
        const body = await r.json().catch(() => null)
        const detail =
          typeof body?.detail === 'string' ? body.detail : `${r.status} ${r.statusText}`
        return setИтог({ ok: false, text: detail })
      }
      const новая = (await r.json()) as Setting
      setS(новая)
      setValue(String(новая.value))
      setИтог({ ok: true, text: 'сохранено' })
    } catch (e) {
      setИтог({ ok: false, text: errorMessage(e) })
    }
  }

  return (
    <tr style="border-bottom:1px solid var(--border-subtle)">
      <td class="px-2 py-2">
        {подпись}
        <div class="text-xs" style="color:var(--text-muted)">
          {s.key}
        </div>
      </td>
      <td class="px-2 py-2">
        <form id={`setting-${s.key}`} onSubmit={сохранить}>
          <input
            type="text"
            inputMode="decimal"
            aria-label={подпись}
            value={value}
            onInput={(e) => setValue((e.target as HTMLInputElement).value)}
            class="px-2 py-1 rounded text-sm num w-32"
            style={inputStyle}
          />
        </form>
      </td>
      <td class="px-2 py-2">{s.unit ?? '—'}</td>
      <td class="px-2 py-2 num">{formatDateTime(s.changed_at)}</td>
      <td class="px-2 py-2">
        <button
          type="submit"
          form={`setting-${s.key}`}
          class="px-3 py-1 rounded text-sm"
          style={inputStyle}
        >
          Сохранить
        </button>
        {итог && (
          <span
            role="status"
            class="ml-2"
            style={`color:var(--state-${итог.ok ? 'success' : 'error'})`}
          >
            {итог.text}
          </span>
        )}
      </td>
    </tr>
  )
}
