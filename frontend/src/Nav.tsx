import { ROUTES } from './routes'
import { logout, roleLabels, type AuthUser } from './lib/auth'
import { formatDate, formatTime } from './lib/format'
import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from './lib/api'
import { useLastUpdate } from './lib/poll'
import { Logo } from './components/Logo'

const ADMIN_ROUTES = [
  { path: '/admin/directory', label: 'Служба каталогов' },
  { path: '/admin/users', label: 'Пользователи' },
  { path: '/admin/sources', label: 'Источники данных' },
  { path: '/admin/audit', label: 'Журнал действий' },
  { path: '/admin/settings', label: 'Настройки' },
]

const ДАННЫЕ_С = '01.01.2019'

export function Nav({ currentPath, me }: { currentPath: string; me: AuthUser | null }) {
  // Пункт меню виден только администратору — сервер всё равно отвечает 403
  // остальным (НФ-43: скрытие пункта не заменяет отказ по прямому адресу).
  const at = useLastUpdate()
  // Срез берём один раз: шапка живёт всё время работы, а срез двигается раз в сутки.
  // Без свежо(): «обновлена в» — про данные текущего экрана, а не про шапку.
  const [asOf, setAsOf] = useState<string | null>(null)
  useEffect(() => {
    if (!me) return
    let отменено = false
    apiFetch('/api/sensor-risk/summary')
      .then((r) => (r.ok ? r.json() : null))
      .then((s: { as_of: string | null } | null) => !отменено && setAsOf(s?.as_of ?? null))
      .catch(() => {})
    return () => {
      отменено = true
    }
  }, [me])
  const menuRoutes = me?.roles.includes('admin') ? [...ROUTES, ...ADMIN_ROUTES] : ROUTES

  // flex-wrap у шапки и меню: на 390 px без переноса шапка была шире окна — до 812 px
  // с меню администратора (e2e/layout-390.spec.ts).
  return (
    <header
      class="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2.5 min-h-16 text-white"
      style="background:var(--brand-header-bg)"
    >
      <div class="flex items-center gap-3 mr-auto">
        <a href="/map" class="text-white">
          <Logo height={40} />
        </a>
        <span class="text-xs uppercase tracking-wider text-[#B9CCE6]">ОДС · прогноз аварий</span>
      </div>
      <nav aria-label="Разделы" class="flex flex-wrap gap-0.5">
        {menuRoutes.map((r) => {
          const active = currentPath === r.path
          return (
            <a
              key={r.path}
              href={r.path}
              aria-current={active ? 'page' : undefined}
              class={`text-sm no-underline px-3 py-1.5 rounded-md border-b-[3px] transition-colors font-semibold ${active ? '' : 'hover:bg-white/10 hover:!text-white'}`}
              style={
                active
                  ? `background:var(--brand-nav-active); color:#fff; border-bottom-color:var(--brand-nav-marker)`
                  : `color:#DCE8F7; border-bottom-color:transparent`
              }
            >
              {r.label}
            </a>
          )
        })}
      </nav>
      {/* Ширину блока держит вторая строка, она есть всегда: появление «обновлена в»
          сдвигало всё меню на 131 px при переходе (28.09.2026). Первая строка пустая,
          пока экран без опроса.
          Вторая строка — период выгрузки: начало 01.01.2019 (docs/day-one.md), конец —
          срез, на котором считает worker; раньше это была плитка «Срез данных» на главной. */}
      <div class="num text-xs leading-tight text-right" style="color:#B9CCE6; min-width:30ch">
        <div style="min-height:1.25em">{at && `система обновлена в ${formatTime(at)}`}</div>
        <div data-testid="data-period">
          {asOf ? `данные ${ДАННЫЕ_С} — ${formatDate(asOf)}` : `данные с ${ДАННЫЕ_С}`}
        </div>
      </div>
      {me && (
        <div class="flex flex-wrap items-center gap-2.5 text-[13.5px]" style="color:#CFE0F5">
          <span>
            {me.full_name || me.login} · {roleLabels(me.roles)}
          </span>
          <button
            type="button"
            onClick={() => logout().then(() => (window.location.href = '/login'))}
            class="px-3 py-1 rounded-md font-semibold transition-colors hover:bg-white/10"
            style="background:transparent; border:1px solid #6F8DB8; color:#fff"
          >
            Выйти
          </button>
        </div>
      )}
    </header>
  )
}
