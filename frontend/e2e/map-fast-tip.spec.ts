// Подсказка к метке схемы появляется сразу при наведении, а не через ~1 с, как
// браузерный <title> (Слава, 28.09.2026: «при наведении можно быстрее получать инфу»).
// Против живого стенда, только чтение; E2E_BUNDLE=dist — своя сборка.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })
test.beforeEach(({ page }) => свойБандл(page))

test('подсказка к пикету датчиков — сразу при наведении', async ({ page }) => {
  await page.goto('/map')
  const стопка = page.getByTestId('sensor-demo').locator('svg[role="img"] rect:has(> desc)').first()
  await expect(стопка).toBeAttached()
  const тексты = (await page.locator('main svg desc').allTextContents()).map((т) => т.trim())
  await стопка.hover({ force: true })
  // 300 мс — меньше задержки браузерного <title>. Под курсором может оказаться соседняя
  // стопка (они перекрываются), поэтому сверяем с любым <desc> схемы, а не с первым.
  await expect(page.getByRole('tooltip')).toBeVisible({ timeout: 300 })
  expect(тексты).toContain((await page.getByRole('tooltip').textContent())!.trim())
  await page.mouse.move(0, 0)
  await expect(page.getByRole('tooltip')).toHaveCount(0)
})

test('подсказка к метке участка — сразу при наведении', async ({ page }) => {
  await page.goto('/map?axis=sections')
  const метка = page.locator('main svg[role="img"] g:has(> desc)').first()
  await expect(метка).toBeAttached()
  const тексты = (await page.locator('main svg desc').allTextContents()).map((т) => т.trim())
  await метка.hover({ force: true })
  await expect(page.getByRole('tooltip')).toBeVisible({ timeout: 300 })
  expect(тексты).toContain((await page.getByRole('tooltip').textContent())!.trim())
})
