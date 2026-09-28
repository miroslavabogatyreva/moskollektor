// Мок карточки объекта: GET /api/objects/{id}, /channels и /readings — по форме,
// которую ObjectCard.tsx читает со стенда (backend/app/api/objects.py). Для экрана
// шкалы метана (MOS-169, Ф-30): нужны газовые каналы с заданным числом, а живой
// участок с метаном 0,8 % об. в окне по умолчанию не подберёшь.
//
// E2E_OFFLINE=1 — стенда нет вовсе (облачная сессия, BASE_URL на vite preview):
// тогда остальные /api/* отвечают пусто, а /api/auth/me — диспетчером.
import type { Page } from '@playwright/test'

export interface MockChannel {
  channel_id: number
  name: string
  sensor_kind: string
  system_kind: string
  value_num?: number | null
}

const СРЕЗ = '2026-06-25T09:00:00+00:00'

export async function mockObjectCard(page: Page, sectionId: number, channels: MockChannel[]) {
  if (process.env.E2E_OFFLINE === '1') {
    // Регистрируем первым: у Playwright побеждает последний подходящий route.
    await page.route('**/api/**', (route) =>
      route.fulfill({ json: { total: 0, items: [], last_id: null } }),
    )
    await page.route('**/api/alerts/stream', (route) =>
      route.fulfill({ contentType: 'text/event-stream', body: '' }),
    )
    await page.route('**/api/auth/me', (route) =>
      route.fulfill({
        json: {
          login: 'dispatcher1',
          full_name: 'Диспетчер',
          roles: ['dispatcher'],
          auth_source: 'local',
        },
      }),
    )
  }
  const base = `**/api/objects/${sectionId}`
  await page.route(base, (route) =>
    route.fulfill({
      json: {
        section_id: sectionId,
        smvu_key: `mock-${sectionId}`,
        inventory_no: null,
        last_reading_at: СРЕЗ,
        channels: channels.map((c) => ({
          channel_id: c.channel_id,
          tag: `15-${c.channel_id}.`,
          name: c.name,
          system_kind: c.system_kind,
          sensor_kind: c.sensor_kind,
        })),
        current_risk: null,
        open_permits: [],
        recent_forecasts: [],
        dispatcher_objects: [],
      },
    }),
  )
  await page.route(`${base}/channels?*`, (route) =>
    route.fulfill({
      json: {
        total: channels.length,
        items: channels.map((c) => ({
          channel_id: c.channel_id,
          system_kind: c.system_kind,
          sensor_kind: c.sensor_kind,
          name: c.name,
          is_active: true,
          faults_cnt: 0,
          last_fault_at: null,
          avg_duration_h: null,
        })),
      },
    }),
  )
  // Два показания на канал: раннее 0,1 и последнее — заданное; метка обязана встать
  // по последнему. Канал без value_num пишет только состояние «Норма».
  await page.route(`${base}/readings?*`, (route) =>
    route.fulfill({
      json: channels.flatMap((c) =>
        c.value_num === undefined
          ? []
          : [
              {
                read_time: '2026-06-25T07:00:00+00:00',
                channel_id: c.channel_id,
                is_alarm: false,
                value_text: null,
                value_num: 0.1,
              },
              {
                read_time: '2026-06-25T08:00:00+00:00',
                channel_id: c.channel_id,
                is_alarm: false,
                value_text: c.value_num == null ? 'Норма' : null,
                value_num: c.value_num,
              },
            ],
      ),
    }),
  )
}
