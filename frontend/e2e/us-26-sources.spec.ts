// US-26 «Состояние источников данных» (MOS-211), строки приёмки Ф-85, Ф-82, М-04.
// Экран /admin/sources и метод GET /api/sources — только администратор (settings.read).
//
// Источников три: поток СМВУ (эмулятор, MOS-37), метеоданные (эмулятор Open-Meteo,
// MOS-36) и расчёт прогноза. Системы учёта заявок в списке нет: её эмулятора нет
// (US-19), и сценарий 1 в docs/user-stories.md поправлен под это 27.09.2026.
//
// Сценарий 2 требует остановить эмулятор СМВУ на стенде на 10 минут
// (`docker compose stop emulator-smvu`) — это действие на стенде, его делает
// человек, а не тест. Тест включается переменной E2E_SMVU_STOPPED=1 и без неё
// пропускается, а не зеленеет впустую.
import { expect, test } from '@playwright/test'
import { account, demoAccounts, loginAs } from './helpers/auth'

test.use({ extraHTTPHeaders: {} })

const SOURCES = ['Поток СМВУ', 'Метеоданные', 'Расчёт прогноза']

test('US-26 сц. 1: время по каждому источнику', async ({ page }) => {
  const admin = account(await demoAccounts(page), 'admin')
  await loginAs(page, admin.login, admin.password)
  await page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Источники данных' }).click()
  await page.waitForURL('**/admin/sources')
  for (const name of SOURCES) {
    const row = page.getByRole('row', { name: new RegExp(name) })
    await expect(row).toBeVisible()
    // Время последних данных — дата и время, а не пустая ячейка и не «данных не было».
    await expect(row.getByTestId('last-data')).toHaveText(/\d{2}\.\d{2}\.\d{4},? \d{2}:\d{2}/)
  }
})

test('US-26 сц. 2: отставший источник назван', async ({ page }) => {
  test.skip(process.env.E2E_SMVU_STOPPED !== '1', 'эмулятор СМВУ не остановлен: задайте E2E_SMVU_STOPPED=1 после 10 минут остановки')
  const admin = account(await demoAccounts(page), 'admin')
  await loginAs(page, admin.login, admin.password)

  const r = await page.request.get('/api/sources')
  expect(r.status()).toBe(200)
  const smvu = ((await r.json()) as { code: string; lag_s: number | null }[]).find((s) => s.code === 'smvu')
  expect(smvu?.lag_s ?? 0).toBeGreaterThanOrEqual(600)

  await page.goto('/admin/sources')
  const row = page.getByRole('row', { name: /Поток СМВУ/ })
  await expect(row.getByTestId('state')).toHaveText(/отстаёт/)
  const минут = Number((await row.getByTestId('state').innerText()).match(/(\d+) мин/)?.[1])
  expect(минут).toBeGreaterThanOrEqual(10)
})

test('US-26: диспетчеру раздел закрыт (НФ-43)', async ({ page }) => {
  const dispatcher = account(await demoAccounts(page), 'dispatcher')
  await loginAs(page, dispatcher.login, dispatcher.password)
  await expect(
    page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Источники данных' }),
  ).toHaveCount(0)
  expect((await page.request.get('/api/sources')).status()).toBe(403)
})
