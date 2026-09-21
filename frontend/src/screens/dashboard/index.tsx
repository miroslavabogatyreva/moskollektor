import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchRisks } from './api'
import type { RiskRow } from './types'

/* Дашборд рисков — задача 5.2 (MOS-49). Плитки и ранжированный список по риску
   из GET /api/risks (MOS-40 + MOS-32). Цветовая шкала риска (critical/high/…)
   сюда не легла: порог для probability нигде не зафиксирован, а числа сегодня —
   от заглушки модели и решают, по словам расчётной сессии, "не подгонять под
   сегодняшнее". Список сортирую по risk_rank — это готовый порядок от API,
   а не догадка. Строка кликабельна и ведёт на /objects/:sectionId (ObjectCard,
   5.5, MOS-52). */

export function DashboardScreen(_props: Record<string, unknown>) {
  const [rows, setRows] = useState<RiskRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchRisks()
      .then(setRows)
      .catch((e) => setError(String(e)))
  }, [])

  const sorted = useMemo(
    () => (rows ? [...rows].sort((a, b) => a.risk_rank - b.risk_rank) : []),
    [rows],
  )

  const stats = useMemo(() => {
    if (!rows || rows.length === 0) return null
    const stale = rows.filter((r) => r.is_stale).length
    const computedAtMax = rows.reduce(
      (max, r) => (r.as_of > max ? r.as_of : max),
      rows[0].as_of,
    )
    const horizons = new Set(rows.map((r) => r.horizon_h))
    const horizonLabel =
      horizons.size === 1
        ? `${[...horizons][0]} ч`
        : `${Math.min(...horizons)}–${Math.max(...horizons)} ч`
    return { total: rows.length, stale, computedAtMax, horizonLabel }
  }, [rows])

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Дашборд рисков
      </h1>

      {error && <p style="color:var(--state-error)">Не удалось загрузить риски: {error}</p>}
      {!rows && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {stats && (
        <div class="grid gap-3" style="grid-template-columns:repeat(4,minmax(0,1fr))">
          <Tile label="Участков в расчёте" value={String(stats.total)} />
          <Tile
            label="Устаревших расчётов"
            value={String(stats.stale)}
            sub={
              stats.stale > 0
                ? 'расчёт по объекту не прошёл, показан прошлый результат'
                : 'все свежие'
            }
          />
          <Tile
            label="Данные по состоянию на"
            value={new Date(stats.computedAtMax).toLocaleDateString('ru-RU')}
            sub="конец выгрузки заказчика, не время расчёта"
          />
          <Tile label="Горизонт прогноза" value={stats.horizonLabel} />
        </div>
      )}

      {rows && rows.length === 0 && <p style="color:var(--text-muted)">Рисков нет.</p>}

      {sorted.length > 0 && (
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Ранг', 'Объект', 'Вероятность', 'Горизонт', 'Момент среза', 'Статус'].map((h) => (
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
            {sorted.map((r) => (
              <tr
                key={r.section_id}
                onClick={() => route(`/objects/${r.section_id}`)}
                style="border-bottom:1px solid var(--border-subtle); cursor:pointer"
              >
                <td class="px-2 py-2 num">{r.risk_rank}</td>
                <td class="px-2 py-2 num">{r.section_id}</td>
                <td class="px-2 py-2 num">{r.probability.toFixed(4)}</td>
                <td class="px-2 py-2 num">{r.horizon_h} ч</td>
                <td class="px-2 py-2 num">{new Date(r.as_of).toLocaleString('ru-RU')}</td>
                <td class="px-2 py-2" style={r.is_stale ? 'color:var(--state-warning)' : undefined}>
                  {r.is_stale ? 'устарело' : 'свежий'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <article
      class="p-3 rounded flex flex-col gap-1"
      style="background:var(--bg-surface); border:1px solid var(--border-subtle)"
    >
      <h3 class="text-xs uppercase tracking-wide" style="color:var(--text-muted)">
        {label}
      </h3>
      <div class="num text-2xl font-semibold" style="font-family:var(--font-display)">
        {value}
      </div>
      {sub && (
        <div class="text-xs" style="color:var(--text-secondary)">
          {sub}
        </div>
      )}
    </article>
  )
}
