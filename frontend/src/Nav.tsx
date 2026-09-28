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
      {/* Место под «обновлено в» держим всегда: экран без опроса его не пишет, и
          появление подписи сдвигало всё меню на 131 px при переходе (28.09.2026). */}
      <span
        class="num text-xs"
        style={`color:#B9CCE6${at ? '' : '; visibility:hidden'}`}
        aria-hidden={at ? undefined : 'true'}
      >
        обновлено в {at ? formatTime(at) : '00:00:00'}
      </span>
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
