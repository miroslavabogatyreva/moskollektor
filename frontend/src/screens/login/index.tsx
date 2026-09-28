import { useEffect, useState } from 'preact/hooks'
import { errorMessage } from '../../lib/format'
import { login, ROLE_LABELS } from '../../lib/auth'
import { Logo } from '../../components/Logo'
import { fetchAuthInfo } from './api'
import type { DemoAccount } from './types'

/* Экран входа — MOS-39 (Q4.2). Решение Славы 24.09.2026: логин/пароль,
   LDAP с запасным входом по локальной записи. Таблица демо-учёток —
   пункт 2 того же решения, показывается только если бэк прислал
   demo_accounts (AUTH_DEMO_HINTS=1 на стенде, 0 в эксплуатации). */

// next — куда вернуться после входа (apiFetch дописывает его при 401).
// Не уводим повторно на /login: без проверки список демо-учёток из
// GET /api/auth/info привёл бы сюда самого себя строкой в адресе.
//
// Открытый редирект (нашла 92, 24.09.2026): next — часть адреса, значит
// её пишет не только apiFetch, а кто угодно в ссылке жертве. window.location.href
// без проверки увёл бы её на чужой хост чужим паролем от НАШЕГО экрана входа.
// Список запрещённых символов чинить бесконечно: браузер вырезает \t, \n, \r
// из адреса ПЕРЕД разбором (WHATWG URL Standard), и /\t/evil.example
// становится //evil.example уже после проверки на '//' (нашла 92 второй
// раз). Поэтому next разбирает тот же парсер, что и сам переход: любая
// строка, которую браузер в итоге сведёт к чужому origin, будет поймана
// здесь так же, как её поймал бы сам переход.
function nextPath(): string {
  const next = new URLSearchParams(window.location.search).get('next')
  if (!next) return '/map'
  try {
    const u = new URL(next, window.location.origin)
    return u.origin === window.location.origin && u.pathname !== '/login'
      ? u.pathname + u.search + u.hash
      : '/map'
  } catch {
    return '/map'
  }
}

export function LoginScreen(_props: Record<string, unknown>) {
  const [loginValue, setLoginValue] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [demoAccounts, setDemoAccounts] = useState<DemoAccount[]>([])

  useEffect(() => {
    fetchAuthInfo()
      .then((info) => setDemoAccounts(info.demo_accounts))
      .catch(() => setDemoAccounts([]))
  }, [])

  async function submit(e: Event) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(loginValue, password)
      // Полная перезагрузка, не route(): App.tsx спрашивает «кто вошёл» один
      // раз при монтировании, и после входа это состояние должно быть свежим,
      // а не вчерашним null из предыдущей отрисовки.
      window.location.href = nextPath()
    } catch (err) {
      setError(errorMessage(err))
      setSubmitting(false)
    }
  }

  return (
    <main class="p-5 flex flex-col gap-6 items-center">
      <div class="w-full flex flex-col gap-4" style="max-width:320px">
        <h1 class="self-center mt-6 mb-2" style="color:var(--brand)">
          <Logo height={120} />
        </h1>

        <form onSubmit={submit} class="flex flex-col gap-3">
          <label class="flex flex-col gap-1 text-sm" style="color:var(--text-secondary)">
            Логин
            <input
              type="text"
              autocomplete="username"
              value={loginValue}
              onInput={(e) => setLoginValue((e.target as HTMLInputElement).value)}
              class="px-2 py-1.5 rounded text-sm"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            />
          </label>
          <label class="flex flex-col gap-1 text-sm" style="color:var(--text-secondary)">
            Пароль
            <input
              type="password"
              autocomplete="current-password"
              value={password}
              onInput={(e) => setPassword((e.target as HTMLInputElement).value)}
              class="px-2 py-1.5 rounded text-sm"
              style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
            />
          </label>

          {error && <p style="color:var(--state-error)">{error}</p>}

          <button
            type="submit"
            disabled={submitting || !loginValue || !password}
            class="px-3 py-1.5 rounded text-sm disabled:opacity-50"
            style="background:var(--brand-action); color:var(--text-on-brand)"
          >
            {submitting ? 'Входим…' : 'Войти'}
          </button>
        </form>
      </div>

      {demoAccounts.length > 0 && (
        <div class="w-full flex flex-col gap-2" style="max-width:640px">
          <h2 class="text-sm font-semibold" style="color:var(--text-secondary)">
            Демо-учётные записи
          </h2>
          <table class="w-full text-sm" style="border-collapse:collapse">
            <thead>
              <tr>
                {['Логин', 'Пароль', 'Роль', 'Что видит', ''].map((h) => (
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
              {demoAccounts.map((a) => (
                <tr key={a.login} style="border-bottom:1px solid var(--border-subtle)">
                  <td class="px-2 py-2 num">{a.login}</td>
                  <td class="px-2 py-2 num">{a.password}</td>
                  <td class="px-2 py-2">
                    {a.role_name || ROLE_LABELS[a.role_code] || a.role_code}
                  </td>
                  <td class="px-2 py-2" style="color:var(--text-secondary)">
                    {a.sees}
                  </td>
                  <td class="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => {
                        setLoginValue(a.login)
                        setPassword(a.password)
                        setError(null)
                      }}
                      class="px-2 py-1 rounded text-xs"
                      style="background:var(--bg-surface); border:1px solid var(--border-strong); color:var(--text-primary)"
                    >
                      Войти как
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
