import { useEffect, useRef, useState } from 'preact/hooks'
import { apiFetch } from '../lib/api'
import { errorMessage } from '../lib/format'

/* Диалог решения диспетчера — задача 5.8 (MOS-55), приёмка Ф-92, история US-09.
   Решение — только из закрытого справочника ref.dispatcher_decision (четыре кода),
   свободный текст — только комментарий. Пока решение не выбрано, «Сохранить»
   неактивна и запроса в сети нет (US-09 сц. 2); сервер то же требование держит
   сам и отвечает 422 (POST /api/forecasts/{id}/feedback, backend/app/api/routes.py).
   «Ложное срабатывание» дописывает в базу verdict = 0, а CHECK из 004_events.sql
   требует для него причину — поэтому при этом решении диалог просит ещё и её.
   Нативный <dialog> с showModal(): фокус, Esc и роль dialog браузер даёт сам. */

export interface Decision {
  feedback_id: number
  decision_code: string
  decision_name: string
  reason_code: string | null
  reason_name: string | null
  comment: string | null
  verified_externally: boolean
  decided_by: string
  decided_at: string
}

interface Item {
  code: string
  name: string
}

const FIELD =
  'background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)'

export function VerdictDialog({
  forecastId,
  onSaved,
  onClose,
}: {
  forecastId: number
  onSaved: (d: Decision) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [options, setOptions] = useState<{ decisions: Item[]; reasons: Item[] } | null>(null)
  const [decision, setDecision] = useState('')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [verified, setVerified] = useState(false)
  const [busy, setBusy] = useState(false)
  // Две разные беды — две разные надписи: справочник не загрузился (выбирать не из
  // чего) и сохранение не прошло (выбор есть, сервер отказал).
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

  const needsReason = decision === 'false_alarm'
  const hint = !decision ? 'Выберите решение' : needsReason && !reason ? 'Выберите причину' : null
  const canSave = !hint && !busy

  async function save() {
    if (!canSave) return
    setBusy(true)
    setError(null)
    try {
      const r = await apiFetch(`/api/forecasts/${forecastId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision_code: decision,
          reason_code: needsReason ? reason : null,
          comment: comment.trim() || null,
          verified_externally: verified,
        }),
      })
      if (!r.ok) {
        // 403 — роль без права решения (техник): сервер сам пишет, какой роли не хватает.
        const body = await r.json().catch(() => null)
        throw new Error(
          typeof body?.detail === 'string' ? body.detail : `${r.status} ${r.statusText}`,
        )
      }
      const saved = (await r.json()) as Decision
      onSaved(saved)
      // Сначала штатный close(): пока <dialog> модальный, остальная страница инертна,
      // и вернуть фокус на кнопку нельзя. Событие close снимет диалог (onClose).
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
      aria-labelledby="verdict-title"
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
        <h2 id="verdict-title" class="text-base font-semibold">
          Решение диспетчера
        </h2>

        <label class="flex flex-col gap-1">
          Решение
          <select
            class="px-2 py-1 rounded"
            style={FIELD}
            name="decision_code"
            aria-describedby={!decision ? 'verdict-hint' : undefined}
            value={decision}
            onChange={(e) => setDecision((e.target as HTMLSelectElement).value)}
          >
            <option value="">— выберите —</option>
            {options?.decisions.map((d) => (
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
              aria-describedby={!reason ? 'verdict-hint' : undefined}
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
          <p id="verdict-hint" role="status" style="color:var(--text-muted)">
            {hint}
          </p>
        )}

        <label class="flex flex-col gap-1">
          Комментарий
          <textarea
            rows={3}
            class="px-2 py-1 rounded"
            style={FIELD}
            name="comment"
            maxLength={2000}
            value={comment}
            onInput={(e) => setComment((e.target as HTMLTextAreaElement).value)}
          />
        </label>

        {/* Шаг 4 сценария ТЗ разд. 12 — камер нет, отметка вместо них (HLD разд. 11.6, Ф-91). */}
        <label class="flex items-center gap-2">
          <input
            type="checkbox"
            name="verified_externally"
            checked={verified}
            onChange={(e) => setVerified((e.target as HTMLInputElement).checked)}
          />
          Проверено по внешним источникам
        </label>

        {loadError && (
          <p style="color:var(--state-error)">Не удалось загрузить справочник: {loadError}</p>
        )}
        {error && <p style="color:var(--state-error)">Не удалось сохранить: {error}</p>}

        <div class="flex gap-2 justify-end">
          <button type="button" onClick={() => ref.current?.close()} class="btn btn-secondary">
            Отмена
          </button>
          <button type="submit" disabled={!canSave} class="btn btn-primary">
            Сохранить
          </button>
        </div>
      </form>
    </dialog>
  )
}
