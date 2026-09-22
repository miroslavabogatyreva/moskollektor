import { useEffect, useMemo, useState } from 'preact/hooks'
import { route } from 'preact-router'
import { fetchDataStatus, fetchRisks, fetchSections } from './api'
import { errorMessage } from '../../lib/format'
import { отставание } from './lag'
import { имяОбъекта, словоРиска, указатель, цветРиска } from './rows'
import type { SectionRef } from './rows'
import type { DataStatus, RiskRow } from './types'

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
  /* Состояние данных отдельным запросом и отдельной ошибкой: край выгрузки
     не выводится из прогнозов (MOS-148), и его недоступность не должна гасить
     список рисков — это две разные беды, и на экране они выглядят по-разному. */
  const [status, setStatus] = useState<DataStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  /* Справочник участков — чтобы назвать объект словами (MOS-127). Его отказ
     не гасит ни список, ни плитки: без него в столбце «Объект» останется
     номер участка, и таблица работает дальше. */
  const [sections, setSections] = useState<SectionRef[] | null>(null)

  useEffect(() => {
    fetchRisks()
      .then(setRows)
      .catch((e) => setError(errorMessage(e)))
    fetchDataStatus()
      .then(setStatus)
      .catch((e) => setStatusError(errorMessage(e)))
    fetchSections()
      .then(setSections)
      .catch(() => setSections([]))
  }, [])

  const sorted = useMemo(
    () => (rows ? [...rows].sort((a, b) => a.risk_rank - b.risk_rank) : []),
    [rows],
  )

  const имена = useMemo(() => указатель(sections ?? []), [sections])

  const stats = useMemo(() => {
    if (!rows || rows.length === 0) return null
    const stale = rows.filter((r) => r.is_stale).length
    const horizons = new Set(rows.map((r) => r.horizon_h))
    const horizonLabel =
      horizons.size === 1
        ? `${[...horizons][0]} ч`
        : `${Math.min(...horizons)}–${Math.max(...horizons)} ч`
    return { total: rows.length, stale, horizonLabel }
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
          <DataEdgeTile status={status} error={statusError} />
          <Tile label="Горизонт прогноза" value={stats.horizonLabel} />
        </div>
      )}

      {rows && rows.length === 0 && <p style="color:var(--text-muted)">Рисков нет.</p>}

      {sorted.length > 0 && (
        <table class="w-full text-sm" style="border-collapse:collapse">
          <thead>
            <tr>
              {['Ранг', 'Объект', 'Риск', 'Вероятность'].map((h) => (
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
                style={`border-bottom:1px solid var(--border-subtle); border-left:3px solid ${цветРиска(r.risk_class)}; cursor:pointer`}
              >
                <td class="px-2 py-2 num">{r.risk_rank}</td>
                <td class="px-2 py-2">
                  {имяОбъекта(имена.get(r.section_id), r.section_id)}{' '}
                  <span style="color:var(--text-muted)" class="num">
                    · {r.section_id}
                  </span>
                </td>
                <td class="px-2 py-2">
                  {словоРиска(r.risk_class)}
                  {r.is_stale && (
                    <span style="color:var(--state-warning)">
                      {' '}
                      · расчёт не прошёл, показан прошлый
                    </span>
                  )}
                </td>
                <td class="px-2 py-2 num">{r.probability.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}

/* Край выгрузки и момент расчёта — два разных числа под двумя разными подписями
   (MOS-148). Раньше здесь стояло одно: дашборд брал max(as_of) по строкам ответа
   и подписывал его концом выгрузки. Пока срез назначает планировщик, эти числа
   совпадают; прогон, запущенный руками с другим срезом, показывал на этой плитке
   дату, которой в данных нет — снято на стенде 22.09.2026, прогон 501. */
function DataEdgeTile({ status, error }: { status: DataStatus | null; error: string | null }) {
  if (error) {
    return (
      <Tile
        label="Данные по состоянию на"
        value="—"
        sub={`не удалось спросить у сервера: ${error}`}
      />
    )
  }
  if (!status) {
    return <Tile label="Данные по состоянию на" value="…" sub="спрашиваем сервер" />
  }
  if (!status.data_edge) {
    /* Пустое место здесь читается как ноль, поэтому говорим словами. */
    return (
      <Tile
        label="Данные по состоянию на"
        value="неизвестно"
        sub="суточная свёртка пуста, краю выгрузки взяться неоткуда"
      />
    )
  }
  /* Отставание среза от края — MOS-129. Сервер считает его сам (`lag_days`),
     браузер только подписывает: вычитание двух дат здесь пошло бы в поясе
     того, кто смотрит. Когда срез и край сошлись — а на плановом прогоне они
     сходятся всегда — про срез молчим: две даты под одной плиткой диспетчер
     читает как одну, и лишняя строка «отставание 0» перестаёт замечаться
     ровно тогда, когда в ней появляется число. */
  const разрыв = отставание(status.lag_days)
  return (
    <Tile
      label="Данные по состоянию на"
      value={new Date(status.data_edge).toLocaleDateString('ru-RU')}
      sub="конец выгрузки заказчика, по всему парку сразу"
      note={
        status.computed_at
          ? `расчёт от ${new Date(status.computed_at).toLocaleString('ru-RU')}` +
            (разрыв.разошлись ? '' : `, ${разрыв.текст}`)
          : 'расчёта ещё не было'
      }
      warn={
        разрыв.разошлись && status.as_of
          ? `срез расчёта — ${new Date(status.as_of).toLocaleDateString('ru-RU')}: ${разрыв.текст}`
          : undefined
      }
    />
  )
}

function Tile({
  label,
  value,
  sub,
  note,
  warn,
}: {
  label: string
  value: string
  sub?: string
  note?: string
  warn?: string
}) {
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
      {note && (
        <div class="text-xs" style="color:var(--text-muted)">
          {note}
        </div>
      )}
      {warn && (
        <div class="text-xs" style="color:var(--state-warning)">
          {warn}
        </div>
      )}
    </article>
  )
}
