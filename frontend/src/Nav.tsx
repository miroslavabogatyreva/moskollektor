import { ROUTES } from './routes'
import { logout, roleLabels, type AuthUser } from './lib/auth'
import { formatTime } from './lib/format'
import { useLastUpdate } from './lib/poll'
import { Logo } from './components/Logo'

const ADMIN_ROUTES = [
  { path: '/admin/directory', label: 'Служба каталогов' },
  { path: '/admin/users', label: 'Пользователи' },
  { path: '/admin/sources', label: 'Источники данных' },
  { path: '/admin/audit', label: 'Журнал действий' },
  { path: '/admin/settings', label: 'Настройки' },
]

export function Nav({ currentPath, me }: { currentPath: string; me: AuthUser | null }) {
  // Пункт меню виден только администратору — сервер всё равно отвечает 403
  // остальным (НФ-43: скрытие пункта не заменяет отказ по прямому адресу).
  const at = useLastUpdate()
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
              class="text-[13.5px] no-underline px-3 py-1.5 rounded-sm border-b-[3px]"
              style={
                active
                  ? `background:var(--brand-nav-active); color:#fff; border-bottom-color:var(--brand-nav-marker)`
                  : `color:#CFE0F5; border-bottom-color:transparent`
              }
            >
              {r.label}
            </a>
          )
        })}
      </nav>
      {at && (
        <span class="num text-xs" style="color:#B9CCE6">
          обновлено в {formatTime(at)}
        </span>
      )}
      {me && (
        <div class="flex flex-wrap items-center gap-2.5 text-[13.5px]" style="color:#CFE0F5">
          <span>
            {me.full_name || me.login} · {roleLabels(me.roles)}
          </span>
          <button
            type="button"
            onClick={() => logout().then(() => (window.location.href = '/login'))}
            class="px-2 py-1 rounded-sm no-underline"
            style="background:transparent; border:1px solid #4A6690; color:#CFE0F5"
          >
            Выйти
          </button>
        </div>
      )}
    </header>
  )
}
