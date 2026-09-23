// US-03. Переходить между разделами из меню — docs/user-stories.md, приёмка М-07.
// Названия test() — дословно названия сценариев истории.
import { expect, test } from '@playwright/test'

test('US-03 сц. 1: текущий раздел подсвечен', async ({ page }) => {
  await page.goto('/log')
  const menu = page.getByRole('navigation', { name: 'Разделы' })
  await expect(menu.getByRole('link', { name: 'Журнал прогнозов' })).toHaveAttribute(
    'aria-current',
    'page',
  )
  // Подсвечен ровно один пункт, а не «журнал и ещё какой-нибудь».
  await expect(menu.locator('[aria-current="page"]')).toHaveCount(1)
})

test('US-03 сц. 2: перезагрузка не теряет раздел', async ({ page }) => {
  await page.goto('/map')
  const ответ = await page.reload()
  expect(ответ?.status()).toBe(200)
  await expect(page).toHaveURL(/\/map$/)
  await expect(
    page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Карта объектов' }),
  ).toHaveAttribute('aria-current', 'page')
})
