// Тур по системе для показа экспертам (28.09.2026): кнопка «Тур по системе» в шапке
// ведёт по главным экранам, на каждом шаге подсвечен элемент и есть подсказка.
// Против живого стенда, только чтение; E2E_BUNDLE=dist — своя сборка.
import { expect, test, type Page } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

// TOUR_LOGIN=tech1 или admin1 — тот же тур под другой ролью.
test.use({ extraHTTPHeaders: { 'X-User-Login': process.env.TOUR_LOGIN ?? 'ods1' } })
test.beforeEach(({ page }) => свойБандл(page))

async function начать(page: Page) {
  await page.goto('/map')
  await page.getByRole('button', { name: 'Тур по системе' }).click()
  await expect(page.locator('.driver-popover')).toBeVisible({ timeout: 15_000 })
}

// Жмёт «Далее» до «Готово» и возвращает заголовки показанных шагов.
async function пройти(page: Page) {
  await начать(page)
  const поповер = page.locator('.driver-popover')
  const заголовки: string[] = []
  for (let i = 0; i < 30; i++) {
    await expect(поповер).toBeVisible({ timeout: 15_000 })
    await expect(поповер.locator('.driver-popover-description')).not.toBeEmpty()
    const заголовок = (await поповер.locator('.driver-popover-title').textContent()) ?? ''
    expect(заголовок, `шаг ${i + 1} с заголовком`).not.toBe('')
    await expect(поповер.locator('.driver-popover-progress-text')).toHaveText(/^\d+ из \d+$/)
    // Подсвечен ровно один элемент, и он хотя бы частью в окне. Прокрутка к нему
    // плавная — опрашиваем, а не смотрим один раз.
    await expect(page.locator('.driver-active-element')).toHaveCount(1)
    await expect
      .poll(
        () =>
          page.locator('.driver-active-element').evaluate((e) => {
            const r = e.getBoundingClientRect()
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight
          }),
        { message: `шаг «${заголовок}»: элемент в окне` },
      )
      .toBe(true)
    // И остаётся в окне, когда экран догрузился: на /orders список заявок приходит
    // позже подсветки, и без сброса прокрутки блок поиска уезжал за край.
    await page.waitForTimeout(1000)
    expect(
      await page.locator('.driver-active-element').evaluate((e) => {
        const r = e.getBoundingClientRect()
        return r.bottom > 0 && r.top < innerHeight
      }),
      `шаг «${заголовок}»: элемент в окне через секунду`,
    ).toBe(true)
    заголовки.push(заголовок)
    const далее = поповер.locator('.driver-popover-next-btn')
    const надпись = (await далее.textContent())?.trim()
    await далее.click()
    if (надпись === 'Готово') break
    // Ждём, пока поповер сменит шаг: переход между экранами идёт асинхронно.
    await expect(поповер.locator('.driver-popover-title')).not.toHaveText(заголовок, {
      timeout: 20_000,
    })
  }
  await expect(поповер).toBeHidden()
  console.log(`${заголовки.length} шагов: ${заголовки.join(' | ')}`)
  return заголовки
}

for (const ширина of [1440, 390]) {
  test(`тур проходит все шаги до «Готово», ${ширина} px`, async ({ page }) => {
    test.setTimeout(180_000)
    await page.setViewportSize({ width: ширина, height: ширина > 800 ? 900 : 844 })
    const ошибки: string[] = []
    page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))
    page.on('pageerror', (e) => ошибки.push(e.message))
    const заголовки = await пройти(page)
    expect(заголовки.length, заголовки.join(' | ')).toBe(14)
    expect(ошибки).toEqual([])
  })
}

// Положительный контроль пропуска: под живыми ролями стенда видны все 14 шагов,
// поэтому участок для карточки отнимаем подменой ответа — три шага карточки
// должны пропасть, а тур дойти до «Готово».
test('шаги экрана, которого нет, пропускаются', async ({ page }) => {
  test.setTimeout(180_000)
  await page.route(
    (u) => u.pathname === '/api/sensor-risk' && u.searchParams.get('limit') === '20',
    (r) => r.fulfill({ json: { items: [], total: 0 } }),
  )
  const заголовки = await пройти(page)
  expect(заголовки).not.toContain('Отказы по каналам')
  expect(заголовки.length).toBe(11)
})

test('Esc закрывает тур, фокус внутри подсказки', async ({ page }) => {
  await начать(page)
  const фокусВнутри = await page.evaluate(
    () => !!document.activeElement?.closest('.driver-popover'),
  )
  expect(фокусВнутри).toBe(true)
  await page.keyboard.press('Escape')
  await expect(page.locator('.driver-popover')).toBeHidden()
  await expect(page.locator('.driver-active-element')).toHaveCount(0)
})
