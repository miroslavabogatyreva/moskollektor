// Вёрстка на телефоне, 390 px (iPhone 12–15). Находки SL.5 (эпик MOS-248), вне эпика:
// шапка Nav.tsx на 390 px была шире окна — до 812 px на любом экране; кнопка «вся линия»
// оси AxisLine.tsx вылезала за окно на 3 px. Ось пикетов под кнопками — SVG
// с viewBox, она сжимается сама; вылезает только ряд кнопок, если он не переносится.
import { expect, test, type Page } from '@playwright/test'

test.use({ viewport: { width: 390, height: 844 } })

const правыйКрай = (page: Page, селектор: string) =>
  page.evaluate(
    (s) =>
      Math.max(...[...document.querySelectorAll(s)].map((e) => e.getBoundingClientRect().right)),
    селектор,
  )

test('шапка на 390 px не шире окна', async ({ page }) => {
  for (const адрес of ['/dashboard', '/orders']) {
    await page.goto(адрес)
    await expect(page.getByRole('navigation', { name: 'Разделы' })).toBeVisible()
    expect(await правыйКрай(page, 'header, header *'), адрес).toBeLessThanOrEqual(390)
    expect(await page.evaluate(() => document.documentElement.scrollWidth), адрес).toBe(390)
  }
})

test('кнопка «вся линия» оси пикетов на 390 px не вылезает за окно', async ({ page }) => {
  await page.goto('/map')
  const кнопка = page.getByRole('group', { name: /^Масштаб линии / }).first()
  await expect(кнопка.getByRole('button', { name: 'вся линия' })).toBeVisible()
  expect(await правыйКрай(page, '[aria-label^="Масштаб линии"] button')).toBeLessThanOrEqual(390)
})
