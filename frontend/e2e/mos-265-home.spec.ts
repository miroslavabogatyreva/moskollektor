// US-01 сц. 6 (MOS-265, план 5.41): смена начинается со схемы пикетов, как рабочее
// место СМВУ 2.0 (docs/meetings/img-forum/19-10-интерфейс-смву-крупно.png).
// Против живого стенда, только чтение; E2E_BUNDLE=dist подменяет фронт стенда своей
// сборкой (helpers/sensor-mock.ts), API остаётся настоящим. Единственный POST — вход.
import { expect, test, type Page } from '@playwright/test'
import { demoAccounts } from './helpers/auth'
import { свойБандл } from './helpers/sensor-mock'

test.beforeEach(({ page }) => свойБандл(page))

// Ожидание — из ответа сервера под той же сессией, что и экран, а не константой.
async function самыйРискованный(page: Page): Promise<number> {
  const r = await page.request.get('/api/sensor-risk/summary?synthetic=1')
  expect(r.ok()).toBe(true)
  const s = (await r.json()) as { top_collectors: { collector_id: number; high: number }[] }
  expect(s.top_collectors.length, 'на стенде есть коллектор с ▲').toBeGreaterThan(0)
  return s.top_collectors[0].collector_id
}

test('US-01 сц. 6: смена начинается со схемы пикетов', async ({ page }) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))

  // disp2 — диспетчер с суженной зоной (коллекторы 5 и 7), на экране входа он есть.
  const уч = (await demoAccounts(page)).find((a) => a.login === 'disp2')!
  await page.goto('/login')
  await page.getByLabel('Логин').fill(уч.login)
  await page.getByLabel('Пароль').fill(уч.password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).toHaveURL(/\/map$/)

  const топ = await самыйРискованный(page)
  await expect(page.locator('main select').first()).toHaveValue(String(топ))

  // На оси датчиков — только ▲ и ◆. Значки с уровнем есть (иначе «нет normal» ничего
  // не доказывает), а «в норме» нет ни одного.
  const ось = page.getByTestId('sensor-demo').locator('svg[role="img"]')
  await expect(ось.locator('[data-level]').first()).toBeAttached()
  await expect(ось.locator('[data-level="normal"]')).toHaveCount(0)

  await expect(page.getByRole('heading', { name: 'Ждут квитирования' })).toBeVisible()
  await expect(page.getByTestId('home-strip')).toBeVisible()
  expect(ошибки).toEqual([])
})

test('US-01 сц. 6: корень ведёт на схему, на 390 px без горизонтальной прокрутки', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page).toHaveURL(/\/map$/)
  await expect(page.getByTestId('sensor-demo').locator('svg[role="img"]').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Ждут квитирования' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})
