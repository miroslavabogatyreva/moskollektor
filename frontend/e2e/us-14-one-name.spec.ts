// US-14 «Один участок — одно имя» — docs/user-stories.md, MOS-243 (план 5.38), приёмка НФ-71.
// Эталон имени — сервер: asset.func_location.name = 'Коллектор ' || префикс smvu_key ||
// ', пикет ' || пикет (db/migrations/010_orders.sql), его отдаёт GET /api/orders
// полем object_name. Дашборд до MOS-243 собирал имя из collector файла sections.json,
// а тот после MOS-181 — номер коллектора дерева (16 значений), не префикс тега:
// участок 2300 звался «Коллектор 15, пикет 730» на дашборде и «Коллектор 884, пикет 730»
// в заявке.
//
// Участок берём живой, а не константой: нужен тот, у которого есть заявка И прогноз
// на первой странице журнала за сегодня (журнал по умолчанию показывает сегодня,
// 200 строк). На стенде 27.09.2026 таких 18 из 357 заявок.
import { expect, test } from '@playwright/test'

interface Участок {
  section_id: number
  smvu_key: string
  collector: number
}

function сегодня(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

test('US-14 сц. 1: одно имя на пяти экранах', async ({ page }) => {
  const участки = (await (await page.request.get('/data/sections.json')).json()) as Участок[]
  const поКлючу = new Map(участки.map((у) => [у.smvu_key, у]))
  const заявки = (
    (await (await page.request.get('/api/orders?limit=1000')).json()) as {
      items: { id: number; object_name: string; smvu_key: string }[]
    }
  ).items
  const журнал = (
    (await (
      await page.request.get(`/api/forecasts?from=${сегодня()}&to=${сегодня()}&limit=200`)
    ).json()) as {
      items: { section_id: number }[]
    }
  ).items
  const вЖурнале = new Set(журнал.map((r) => r.section_id))
  const заявка = заявки.find((з) => вЖурнале.has(поКлючу.get(з.smvu_key)?.section_id ?? -1))
  expect(заявка, 'нет участка с заявкой и прогнозом на первой странице журнала').toBeTruthy()
  const участок = поКлючу.get(заявка!.smvu_key)!
  const имя = заявка!.object_name
  expect(имя).toMatch(/^Коллектор \d+, пикет \d+$/)

  // 1. Заявка — эталон, сервер.
  await page.goto(`/orders/${заявка!.id}`)
  await expect(page.getByText(имя).first()).toBeVisible()

  // 2. Дашборд.
  await page.goto('/dashboard')
  await expect(page.getByRole('cell', { name: имя, exact: false }).first()).toBeVisible()

  // 3. Карточка участка.
  await page.goto(`/objects/${участок.section_id}`)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(имя)

  // 4. Журнал прогнозов (сегодня, первая страница).
  await page.goto('/log')
  await expect(page.getByRole('cell', { name: имя, exact: true }).first()).toBeVisible()

  // 5. Схема: подсказка метки участка начинается с того же имени.
  await page.goto('/map')
  await page
    .getByRole('combobox', { name: 'Коллектор', exact: true })
    .selectOption(String(участок.collector))
  await expect(
    page
      .locator('main svg[role="img"] g > title')
      .filter({ hasText: `${имя} · участок ${участок.section_id}` }),
  ).toHaveCount(1)
})
