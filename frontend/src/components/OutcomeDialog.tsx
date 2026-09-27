import { useEffect, useRef, useState } from 'preact/hooks'
import { apiFetch } from '../lib/api'
import { errorMessage } from '../lib/format'

/* Исход прогноза — история US-10, приёмка Ф-34, Ф-35, Ф-75. Отдельно от решения
   (VerdictDialog): решение — что диспетчер сделал, когда прогноз пришёл; исход —
   чем прогноз кончился. Три исхода из ref.forecast_outcome (миграция 056), у «ложной»
   причина из пяти обязательна: пока её нет, «Сохранить» неактивна и запроса в сети
   нет (US-10 сц. 2); сервер держит то же правило сам и отвечает 422
   (POST /api/forecasts/{id}/outcome, backend/app/api/routes.py). */

export interface Outcome {
  outcome_id: number
  outcome_code: string
  outcome_name: string
  reason_code: string | null
  reason_name: string | null
  decided_by: string
  decided_at: string
}

interface Item {
  code: string
  name: string
}

const FIELD =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function OutcomeDialog({
  forecastId,
  onSaved,
  onClose,
}: {
  forecastId: number
  onSaved: (o: Outcome) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [options, setOptions] = useState<{ outcomes: Item[]; reasons: Item[] } | null>(null)
  const [outcome, setOutcome] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    ref.current?.showModal()
    apiFetch('/api/dispatcher-decisions')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json()
      })
      .then(setOptions)
      .catch((e) => setLoadError(errorMessage(e)))
  }, [])

  const needsReason = outcome === 'false_alarm'
  const hint = !outcome ? 'Выберите исход' : needsReason && !reason ? 'Выберите причину' : null
  const canSave = !hint && !busy

  async function save() {
    if (!canSave) return
    setBusy(true)
    setError(null)
    try {
      const r = await apiFetch(`/api/forecasts/${forecastId}/outcome`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outcome_code: outcome, reason_code: needsReason ? reason : null }),
      })
      if (!r.ok) {
        const body = await r.json().catch(() => null)
        throw new Error(
          typeof body?.detail === 'string' ? body.detail : `${r.status} ${r.statusText}`,
        )
      }
      onSaved((await r.json()) as Outcome)
      ref.current?.close()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby="outcome-title"
      onClose={onClose}
      class="rounded p-5 w-full max-w-md"
      style="background:var(--bg-surface); color:var(--text-primary); border:1px solid var(--border-strong)"
    >
      <form
        method="dialog"
        class="flex flex-col gap-3 text-sm"
        onSubmit={(e) => {
          e.preventDefault()
          save()
        }}
      >
        <h2 id="outcome-title" class="text-base font-semibold">
          Исход прогноза
        </h2>

        <label class="flex flex-col gap-1">
          Исход
          <select
            class="px-2 py-1 rounded"
            style={FIELD}
            name="outcome_code"
            aria-describedby={!outcome ? 'outcome-hint' : undefined}
            value={outcome}
            onChange={(e) => setOutcome((e.target as HTMLSelectElement).value)}
          >
            <option value="">— выберите —</option>
            {options?.outcomes.map((d) => (
              <option key={d.code} value={d.code}>
                {d.name}
              </option>
            ))}
          </select>
        </label>

        {needsReason && (
          <label class="flex flex-col gap-1">
            Причина
            <select
              class="px-2 py-1 rounded"
              style={FIELD}
              name="reason_code"
              aria-describedby={!reason ? 'outcome-hint' : undefined}
              value={reason}
              onChange={(e) => setReason((e.target as HTMLSelectElement).value)}
            >
              <option value="">— выберите —</option>
              {options?.reasons.map((d) => (
                <option key={d.code} value={d.code}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {hint && (
          <p id="outcome-hint" role="status" style="color:var(--text-muted)">
            {hint}
          </p>
        )}

        {loadError && (
          <p style="color:var(--state-error)">Не удалось загрузить справочник: {loadError}</p>
        )}
        {error && <p style="color:var(--state-error)">Не удалось сохранить: {error}</p>}

        <div class="flex gap-2 justify-end">
          <button
            type="button"
            onClick={() => ref.current?.close()}
            class="px-3 py-1 rounded"
            style={FIELD}
          >
            Отмена
          </button>
          <button
            type="submit"
            disabled={!canSave}
            class="px-3 py-1 rounded disabled:opacity-50"
            style={FIELD}
          >
            Сохранить
          </button>
        </div>
      </form>
    </dialog>
  )
}
