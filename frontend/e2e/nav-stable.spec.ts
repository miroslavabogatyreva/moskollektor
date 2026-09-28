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
  // /log (200 px лишних) и /orders (84 px) шире 390 px и без этой правки — отданы
  // визуальной доработке; когда она придёт, добавить их сюда.
  for (const адрес of ['/map', '/dashboard']) {
    await page.goto(адрес)
    await page.waitForLoadState('networkidle')
    const лишнее = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(лишнее, адрес).toBeLessThanOrEqual(0)
  }
})
