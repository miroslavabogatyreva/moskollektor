// Шапка не дёргается при переходе между разделами (28.09.2026, Слава): пункты меню
// стоят на месте, высота шапки не меняется. Против стенда, только чтение.
import { expect, test, type Page } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' }, viewport: { width: 1440, height: 900 } })

const РАЗДЕЛЫ = ['Карта объектов', 'Дашборд рисков', 'Журнал прогнозов', 'Заявки', 'Журнал событий']

async function снимок(page: Page) {
  await page.waitForLoadState('networkidle')
  const меню = page.getByRole('navigation', { name: 'Разделы' })
  const x = await меню
    .getByRole('link')
    .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)))
  const h = await page
    .locator('header')
    .evaluate((e) => Math.round(e.getBoundingClientRect().height))
  return { x, h }
}

test('переход по разделам: пункты меню и высота шапки на месте', async ({ page }) => {
  await свойБандл(page)
  await page.goto('/map')
  const начало = await снимок(page)
  for (const раздел of [...РАЗДЕЛЫ.slice(1), РАЗДЕЛЫ[0]]) {
    await page
      .getByRole('navigation', { name: 'Разделы' })
      .getByRole('link', { name: раздел })
      .click()
    expect(await снимок(page), `после «${раздел}»`).toEqual(начало)
  }
})

test('на 390 px постоянное место под «обновлено» не даёт горизонтальной прокрутки', async ({
  page,
}) => {
  await свойБандл(page)
  await page.setViewportSize({ width: 390, height: 844 })
  // /log (было 200 px лишних) и /orders (84 px) починила визуальная доработка
  // 28.09.2026: таблица листается внутри своей карточки, а не вся страница.
  for (const адрес of ['/map', '/dashboard', '/log', '/orders']) {
    await page.goto(адрес)
    await page.waitForLoadState('networkidle')
    const лишнее = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(лишнее, адрес).toBeLessThanOrEqual(0)
  }
})

// Когда место кончается и блок пользователя переносится на вторую строку, он остаётся
// у правого края шапки, а не под логотипом (Слава, 28.09.2026, окно ~1000 px).
test('перенесённый блок пользователя стоит справа', async ({ page }) => {
  await свойБандл(page)
  for (const ширина of [1000, 1280, 1440]) {
    await page.setViewportSize({ width: ширина, height: 800 })
    await page.goto('/map')
    const шапка = await page.getByRole('banner').boundingBox()
    const выйти = await page.getByRole('button', { name: 'Выйти' }).boundingBox()
    expect(шапка!.x + шапка!.width - (выйти!.x + выйти!.width), `${ширина} px`).toBeLessThan(40)
  }
})

// Кнопка тура мерцает при каждом входе и гаснет после нажатия; перезагрузка —
// новый вход, снова мерцает: под одной учёткой заходят разные люди (Слава, 28.09.2026).
test('кнопка тура мерцает до нажатия и снова после перезагрузки', async ({ page }) => {
  await свойБандл(page)
  await page.goto('/map')
  const тур = page.getByRole('button', { name: 'Тур по системе' })
  await expect(тур).toHaveClass(/tour-pulse/)
  await тур.click()
  await expect(тур).not.toHaveClass(/tour-pulse/)
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.getByRole('button', { name: 'Тур по системе' })).toHaveClass(/tour-pulse/)
})
