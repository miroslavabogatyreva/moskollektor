import { ROUTES } from './routes'

export function Nav({ currentPath }: { currentPath: string }) {
  return (
    <header class="flex items-center gap-5 px-5 py-2.5 min-h-16 text-white" style="background:var(--brand-header-bg)">
      <div class="flex items-baseline gap-2.5 mr-auto">
        <b style="font-family:var(--font-display)" class="text-[17px] font-semibold tracking-tight">
          Москоллектор
        </b>
        <span class="text-xs uppercase tracking-wider text-[#B9CCE6]">ОДС · прогноз аварий</span>
      </div>
      <nav aria-label="Разделы" class="flex gap-0.5">
        {ROUTES.map((r) => {
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
    </header>
  )
}
