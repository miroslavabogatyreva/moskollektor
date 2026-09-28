// «Тип датчика» в общем журнале /tech-events — выпадающий список, а не поле
// «точно, например «Датчик дыма»» (Слава, 28.09.2026). Список отдаёт
// GET /api/tech-events/sensor-kinds. Против живого стенда, только чтение;
// E2E_BUNDLE=dist — своя сборка. Пока метода нет на стенде, подставляем ответ
// той же формы (список строк) из типов первой страницы журнала.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })

interface Страница {
  total: number
  items: { sensor_kind: string | null }[]
}

test('тип датчика выбирается из списка и сужает журнал', async ({ page }) => {
  await свойБандл(page)
  const перваяСтраница = (await (
    await page.request.get('/api/tech-events?limit=1000')
  ).json()) as Страница
  const типыСтраницы = [
    ...new Set(перваяСтраница.items.map((e) => e.sensor_kind).filter((k) => k != null)),
  ] as string[]
  expect(типыСтраницы.length, 'в окне по умолчанию есть события').toBeGreaterThan(0)

  await page.route('**/api/tech-events/sensor-kinds*', async (route) => {
    const r = await route.fetch()
    if (r.ok()) return route.fulfill({ response: r })
    await route.fulfill({ json: [...типыСтраницы].sort() })
  })

  await page.goto('/tech-events')
  const список = page.getByRole('combobox', { name: 'Тип датчика' })
  await expect(список).toBeVisible()
  await expect(список.locator('option').first()).toHaveText('Все')
  // Берём тип, который в окне по умолчанию точно есть, — иначе пустая таблица ничего не доказывает.
  const тип = типыСтраницы[0]
  await expect(список.locator('option', { hasText: тип })).toHaveCount(1)
  await список.selectOption(тип)

  const ответ = page.waitForResponse(
    (r) =>
      r.url().includes('/api/tech-events?') &&
      new URL(r.url()).searchParams.get('sensor_kind') === тип,
  )
  await page.getByRole('button', { name: 'Применить' }).click()
  const тело = (await (await ответ).json()) as Страница
  expect(тело.items.length).toBeGreaterThan(0)
  expect(тело.items.every((e) => e.sensor_kind === тип)).toBe(true)

  const ячейки = page.locator('table tbody tr td:nth-child(3)')
  await expect(ячейки).toHaveCount(тело.items.length)
  expect(new Set(await ячейки.allTextContents())).toEqual(new Set([тип]))
})
