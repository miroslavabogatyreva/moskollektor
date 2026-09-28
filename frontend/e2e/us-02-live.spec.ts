// US-02 сц. 2 «Экран обновляется сам» — docs/user-stories.md, приёмка НФ-89,
// MOS-123 (план 5.13). Один опрос на приложение раз в 60 с (src/lib/poll.ts).
// page.clock сокращает минуту до мгновения; запросы идут на живой стенд.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

// E2E_BUNDLE=dist — проверка шапки до выкладки.
test.beforeEach(async ({ page }) => свойБандл(page))

const времяОбновления = /обновлена в (\d\d:\d\d:\d\d)/

for (const [экран, путь, метод] of [
  ['дашборд', '/dashboard?view=sections', '/api/risks'],
  ['журнал прогнозов', '/log', '/api/forecasts'],
  ['схема коллектора', '/map', '/api/sensor-risk/summary'],
  ['заявки', '/orders', '/api/orders'],
] as const) {
  test(`US-02 сц. 2: экран обновляется сам — ${экран}`, async ({ page }) => {
    await page.clock.install()
    const первый = page.waitForResponse((r) => r.url().includes(метод))
    await page.goto(путь)
    await первый
    const шапка = page.getByRole('banner')
    // Время ставит свежо() после разбора ответа, а не в момент его прихода:
    // /api/risks весит 435 КБ, и чтение шапки сразу после ответа опережало его.
    await expect(шапка, 'в шапке видно время последнего обновления').toContainText(времяОбновления)
    const было = (await шапка.textContent())?.match(времяОбновления)?.[1]

    const повтор = page.waitForRequest((r) => r.url().includes(метод))
    await page.clock.runFor(60_000)
    await повтор
    await expect(шапка).not.toContainText(`обновлена в ${было}`)
    await expect(шапка).toContainText(времяОбновления)
  })
}

// Время в шапке — от удачного ответа экрана, а не от таймера (ревью c0,
// 27.09.2026): после 500 «обновлено» не меняется, а на экране без опроса
// времени нет вовсе.
test('US-02 сц. 2: после ошибки опроса время в шапке не сдвигается', async ({ page }) => {
  await page.clock.install()
  const первый = page.waitForResponse((r) => r.url().includes('/api/risks'))
  await page.goto('/dashboard?view=sections')
  await первый
  const шапка = page.getByRole('banner')
  await expect(шапка).toContainText(времяОбновления)
  const было = (await шапка.textContent())?.match(времяОбновления)?.[1]

  await page.route('**/api/risks', (route) => route.fulfill({ status: 500, body: 'boom' }))
  const повтор = page.waitForResponse((r) => r.url().includes('/api/risks'))
  await page.clock.runFor(60_000)
  expect((await повтор).status()).toBe(500)
  await expect(шапка).toContainText(`обновлена в ${было}`)
})

test('US-02 сц. 2: на экране без опроса времени обновления нет', async ({ page }) => {
  await page.goto('/admin/sources')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('banner')).not.toContainText('обновлена в')
})
