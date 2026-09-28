// Выкладка пересоздаёт контейнер api, и полминуты nginx отвечает 502. Экран не должен
// застревать на «не загрузилось: 502»: apiFetch повторяет GET (lib/api.ts, 28.09.2026).
// Против стенда, только чтение; первые два ответа дерева подменяем на 502.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' } })

test('502 во время выкладки: схема догружается сама, без перезагрузки', async ({ page }) => {
  await свойБандл(page)
  let отказов = 0
  await page.route('**/api/objects/tree', (route) =>
    отказов++ < 2 ? route.fulfill({ status: 502, body: 'Bad Gateway' }) : route.continue(),
  )
  await page.goto('/map')
  await expect(page.getByRole('navigation', { name: 'Дерево объектов' }).getByText('объект Гамма', { exact: true })).toBeVisible({ timeout: 15000 })
  await expect(page.getByText(/Дерево объектов не загрузилось/)).toHaveCount(0)
  expect(отказов).toBeGreaterThanOrEqual(3)
})
