import type { SectionRef } from './rows'
import type { DataStatus, RiskRow } from './types'

// ponytail: вход без пароля, личность берётся из X-User-Login (backend/app/auth/deps.py).
// Заглушка до экрана логина (Q4.2, LDAP) — заменить константу сессией пользователя.
const API_LOGIN = 'dispatcher1'

export async function fetchRisks(): Promise<RiskRow[]> {
  const r = await fetch('/api/risks', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// Край выгрузки спрашиваем у данных, а не выводим из прогнозов (MOS-148).
// Отдельный запрос стоит 5 мс против 435 КБ у /api/risks — плитку можно
// обновлять раз в минуту (НФ-89), не перекачивая список рисков.
export async function fetchDataStatus(): Promise<DataStatus> {
  const r = await fetch('/api/data-status', { headers: { 'X-User-Login': API_LOGIN } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// Справочник участков — тот же файл, который читает схема коллектора
// (screens/map/index.tsx). Нужен, чтобы назвать объект словами: `GET /api/risks`
// отдаёт только section_id (MOS-127). Файл статический, лежит рядом с бандлом,
// заголовок X-User-Login ему не нужен — он не метод API.
//
// Ошибку НЕ глушим до пустого списка молча: без справочника таблица покажет
// «Участок 2477» вместо имени, и это осознанный запасной вид, а не поломка.
// Отдельный запрос и отдельный отказ — по той же причине, что у data-status:
// справочник не доехал и риски не доехали это две разные беды.
export async function fetchSections(): Promise<SectionRef[]> {
  const r = await fetch('/data/sections.json')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
