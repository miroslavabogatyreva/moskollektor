// US-02 сц. 2 «Экран обновляется сам» — docs/user-stories.md, приёмка НФ-89,
// MOS-123 (план 5.13). Один опрос на приложение раз в 60 с (src/lib/poll.ts).
// page.clock сокращает минуту до мгновения; запросы идут на живой стенд.
import { expect, test } from '@playwright/test'

const времяОбновления = /обновлено в (\d\d:\d\d:\d\d)/

for (const [экран, путь, метод] of [
  ['дашборд', '/dashboard', '/api/risks'],
  ['журнал прогнозов', '/log', '/api/forecasts'],
] as const) {
  test(`US-02 сц. 2: экран обновляется сам — ${экран}`, async ({ page }) => {
    await page.clock.install()
    const первый = page.waitForResponse((r) => r.url().includes(метод))
    await page.goto(путь)
    await первый
    const шапка = page.getByRole('banner')
    const было = (await шапка.textContent())?.match(времяОбновления)?.[1]
    expect(было, 'в шапке видно время последнего обновления').toBeTruthy()

    const повтор = page.waitForRequest((r) => r.url().includes(метод))
    await page.clock.runFor(60_000)
    await повтор
    await expect(шапка).not.toContainText(`обновлено в ${было}`)
    await expect(шапка).toContainText(времяОбновления)
  })
}
