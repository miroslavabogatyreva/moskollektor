import { apiFetch } from '../../lib/api'
import type { SectionRef } from './rows'
import type { DataStatus, OrdersSummary, RiskRow, Unacked, WeatherNow } from './types'

export async function fetchRisks(): Promise<RiskRow[]> {
  const r = await apiFetch('/api/risks')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// Край выгрузки спрашиваем у данных, а не выводим из прогнозов (MOS-148).
// Отдельный запрос стоит 5 мс против 435 КБ у /api/risks — плитку можно
// обновлять раз в минуту (НФ-89), не перекачивая список рисков.
export async function fetchDataStatus(): Promise<DataStatus> {
  const r = await apiFetch('/api/data-status')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

// Справочник участков — тот же файл, который читает схема коллектора
// (screens/map/index.tsx). Нужен, чтобы назвать объект словами: `GET /api/risks`
// отдаёт только section_id (MOS-127). Файл статический, лежит рядом с бандлом,
// сессия ему не нужна — он не метод API, credentials здесь лишние.
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

// Погода сейчас: 503 — сервер не достучался до Open-Meteo, полоса пишет «недоступна».
export async function fetchWeatherNow(): Promise<WeatherNow> {
  const r = await apiFetch('/api/weather/now')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function fetchUnacked(): Promise<Unacked> {
  const r = await apiFetch('/api/notifications?acked=false&limit=5')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function fetchOrdersSummary(): Promise<OrdersSummary> {
  const r = await apiFetch('/api/orders?limit=1000')
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const { items } = (await r.json()) as { items: { status: string; due_at: string }[] }
  const открытые = items.filter((o) => o.status !== 'COMPLETED' && o.status !== 'CANCELLED')
  const сейчас = Date.now()
  return {
    open: открытые.length,
    overdue: открытые.filter((o) => Date.parse(o.due_at) < сейчас).length,
  }
}
