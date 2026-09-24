import { ROUTES } from './routes'
import { logout, roleLabels, type AuthUser } from './lib/auth'

const ADMIN_ROUTE = { path: '/admin/directory', label: 'Служба каталогов' }

export function Nav({ currentPath, me }: { currentPath: string; me: AuthUser | null }) {
  // Пункт меню виден только администратору — сервер всё равно отвечает 403
  // остальным (НФ-43: скрытие пункта не заменяет отказ по прямому адресу).
  const menuRoutes = me?.roles.includes('admin') ? [...ROUTES, ADMIN_ROUTE] : ROUTES

  return (
    <header
      class="flex items-center gap-5 px-5 py-2.5 min-h-16 text-white"
      style="background:var(--brand-header-bg)"
    >
      <div class="flex items-baseline gap-2.5 mr-auto">
        <b style="font-family:var(--font-display)" class="text-[17px] font-semibold tracking-tight">
          Москоллектор
        </b>
        <span class="text-xs uppercase tracking-wider text-[#B9CCE6]">ОДС · прогноз аварий</span>
      </div>
      <nav aria-label="Разделы" class="flex gap-0.5">
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
      {me && (
        <div class="flex items-center gap-2.5 text-[13.5px]" style="color:#CFE0F5">
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
