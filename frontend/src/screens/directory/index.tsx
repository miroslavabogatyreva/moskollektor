import { useEffect, useState } from 'preact/hooks'
import { errorMessage } from '../../lib/format'
import { ROLE_LABELS } from '../../lib/auth'
import { checkDirectory, fetchDirectory } from './api'
import type { DirectoryCheckResult, DirectoryInfo } from './types'

/* «Служба каталогов» — MOS-39, решение Славы 24.09.2026, пункт 3: адрес
   каталога, база поиска, шаблон DN, таблица «группа → роль/область», статус
   и «Проверить соединение» — доказывает НФ-76 вживую, без похода в консоль. */

export function DirectoryScreen(_props: Record<string, unknown>) {
  const [info, setInfo] = useState<DirectoryInfo | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<DirectoryCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState<string | null>(null)

  useEffect(() => {
    fetchDirectory()
      .then((r) => {
        if (r.status === 403) {
          setForbidden(true)
          return
        }
        setInfo(r.data)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [])

  async function runCheck() {
    setChecking(true)
    setCheckError(null)
    try {
      setCheck(await checkDirectory())
    } catch (e) {
      setCheckError(errorMessage(e))
    } finally {
      setChecking(false)
    }
  }

  return (
    <main class="p-5 flex flex-col gap-4">
      <h1 style="font-family:var(--font-display)">Служба каталогов</h1>

      {forbidden && (
        <p style="color:var(--state-error)">Доступ запрещён: раздел виден только администратору.</p>
      )}
      {error && <p style="color:var(--state-error)">Не удалось загрузить настройки: {error}</p>}
      {!info && !forbidden && !error && <p style="color:var(--text-muted)">Загрузка…</p>}

      {info && !info.uri && (
        <p style="color:var(--text-muted)">
          Каталог не настроен, вход по локальным учётным записям.
        </p>
      )}

      {info && info.uri && (
        <>
          <dl class="grid gap-x-4 gap-y-1 text-sm" style="grid-template-columns:max-content 1fr">
            <dt style="color:var(--text-muted)">Адрес каталога</dt>
            <dd class="num">{info.uri}</dd>
            <dt style="color:var(--text-muted)">База поиска</dt>
            <dd>{info.base_dn}</dd>
            <dt style="color:var(--text-muted)">Шаблон DN пользователя</dt>
            <dd>{info.user_template}</dd>
          </dl>

          <div class="card p-0 overflow-x-auto">
            <table class="w-full text-sm" style="border-collapse:collapse">
              <thead>
                <tr>
                  {['Группа каталога', 'Роль / область'].map((h) => (
                    <th key={h} class="th">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {info.role_map.map((row) => (
                  <tr key={row.group_cn} style="border-bottom:1px solid var(--border-subtle)">
                    <td class="px-2 py-2">{row.group_cn}</td>
                    <td class="px-2 py-2">
                      {row.role_code
                        ? `роль: ${ROLE_LABELS[row.role_code] ?? row.role_code}`
                        : `область видимости: объект №${row.object_id}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div class="flex items-center gap-3">
            <button type="button" disabled={checking} onClick={runCheck} class="btn btn-primary">
              {checking ? 'Проверяем…' : 'Проверить соединение'}
            </button>
            {checkError && <span style="color:var(--state-error)">{checkError}</span>}
            {check && (
              <span style={`color:var(--state-${check.ok ? 'success' : 'error'})`}>
                {check.ok ? 'Соединение есть' : 'Соединения нет'} · {check.ms} мс · {check.message}
              </span>
            )}
          </div>
        </>
      )}
    </main>
  )
}
