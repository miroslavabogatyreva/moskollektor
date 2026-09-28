// US-01 сц. 6 (MOS-265, план 5.41): смена начинается со схемы пикетов, как рабочее
// место СМВУ 2.0 (docs/meetings/img-forum/19-10-интерфейс-смву-крупно.png).
// Против живого стенда, только чтение; E2E_BUNDLE=dist подменяет фронт стенда своей
// сборкой (helpers/sensor-mock.ts), API остаётся настоящим. Единственный POST — вход.
import { expect, test, type Page } from '@playwright/test'
import { demoAccounts } from './helpers/auth'
import { свойБандл } from './helpers/sensor-mock'

test.beforeEach(({ page }) => свойБандл(page))

// Ожидание — из ответа сервера под той же сессией, что и экран, а не константой.
// top_collectors сервер собирает только из коллекторов с ▲: на срезе 01.06.2026 07:00
// у disp2 (коллекторы 5 и 7) ▲ нет, список пуст, и главная открывает первый свой
// коллектор справочника (map/index.tsx, sections[0]) — это не ошибка сервера.
async function самыйРискованный(page: Page): Promise<number> {
  const r = await page.request.get('/api/sensor-risk/summary?synthetic=1')
  expect(r.ok()).toBe(true)
  const s = (await r.json()) as { top_collectors: { collector_id: number; high: number }[] }
  if (s.top_collectors.length > 0) return s.top_collectors[0].collector_id
  const свои = new Set(
    ((await (await page.request.get('/api/objects/tree')).json()) as { object_id: number }[]).map(
      (к) => к.object_id,
    ),
  )
  const участки = (await (await page.request.get('/data/sections.json')).json()) as {
    collector: number
  }[]
  return участки.find((у) => свои.has(у.collector))!.collector
}

// Сколько рискованных (▲ и ◆) датчиков на коллекторе — чтобы «нет normal» что-то доказывало.
async function рискованных(page: Page, коллектор: number): Promise<number> {
  let всего = 0
  for (const l of ['high', 'watch']) {
    const r = await page.request.get(
      `/api/sensor-risk?synthetic=1&collector=${коллектор}&level=${l}&limit=1`,
    )
    всего += ((await r.json()) as { total: number }).total
  }
  return всего
}

test('US-01 сц. 6: смена начинается со схемы пикетов', async ({ page }) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))

  // disp2 — диспетчер с суженной зоной (коллекторы 5 и 7), на экране входа он есть.
  const уч = (await demoAccounts(page)).find((a) => a.login === 'disp2')!
  await page.goto('/login')
  await page.getByLabel('Логин').fill(уч.login)
  await page.getByLabel('Пароль').fill(уч.password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).toHaveURL(/\/map$/)

  const топ = await самыйРискованный(page)
  await expect(page.locator('main select').first()).toHaveValue(String(топ))

  // На оси датчиков — только ▲ и ◆. Значки с уровнем есть, когда на коллекторе есть
  // рискованные датчики (иначе «нет normal» ничего не доказывает), а «в норме» нет ни одного.
  const ось = page.getByTestId('sensor-demo').locator('svg[role="img"]')
  await expect(ось.first()).toBeVisible()
  if ((await рискованных(page, топ)) > 0)
    await expect(ось.locator('[data-level]').first()).toBeAttached()
  else test.info().annotations.push({ type: 'срез', description: `на коллекторе ${топ} риска нет` })
  await expect(ось.locator('[data-level="normal"]')).toHaveCount(0)

  await expect(page.getByRole('heading', { name: 'Ждут квитирования' })).toBeVisible()
  // Сводная полоса: плитка-ссылка целиком, число — из того же ответа сводки.
  const полоса = page.getByTestId('home-strip')
  const s = (await (await page.request.get('/api/sensor-risk/summary?synthetic=1')).json()) as {
    high: number
    watch: number
  }
  const высокий = полоса.getByRole('link', { name: /Высокий риск/ })
  await expect(высокий).toHaveAttribute('href', '/dashboard?level=high')
  await expect(высокий.locator('.stat-value')).toHaveText(s.high.toLocaleString('ru-RU'))
  const наблюдать = полоса.getByRole('link', { name: /Наблюдать/ })
  await expect(наблюдать).toHaveAttribute('href', '/dashboard?level=watch')
  await expect(наблюдать.locator('.stat-value')).toHaveText(s.watch.toLocaleString('ru-RU'))
  await expect(полоса.getByRole('link', { name: /Заявки в работе/ })).toHaveAttribute(
    'href',
    '/orders?status=active',
  )
  expect(ошибки).toEqual([])
})

test('US-01 сц. 6: корень ведёт на схему, на 390 px без горизонтальной прокрутки', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page).toHaveURL(/\/map$/)
  await expect(page.getByTestId('sensor-demo').locator('svg[role="img"]').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Ждут квитирования' })).toBeVisible()
  // Сравниваем с шириной без полосы прокрутки: с scrollbar-gutter: stable она 375, а не 390.
  const [полная, видимая] = await page.evaluate(() => [
    document.documentElement.scrollWidth,
    document.documentElement.clientWidth,
  ])
  expect(полная).toBeLessThanOrEqual(видимая)
})

// Подписи датчиков в норме у соседних пикетов не налезают друг на друга ни на одном
// шаге приближения: сначала серое число, фраза «N в норме» — только при крупном масштабе.
test('US-01 сц. 6: подписи «в норме» у соседних пикетов не перекрываются', async ({ page }) => {
  const уч = (await demoAccounts(page)).find((a) => a.login === 'disp2')!
  await page.goto('/login')
  await page.getByLabel('Логин').fill(уч.login)
  await page.getByLabel('Пароль').fill(уч.password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).toHaveURL(/\/map$/)

  // Линия с выбранным пикетом: приближение идёт к нему, а у него датчики точно есть.
  const линия = page.getByTestId('sensor-demo').locator('[data-line]:has([data-selected])')
  const ближе = линия.getByRole('button', { name: '+ приблизить' })
  let подписей = 0
  for (let шаг = 0; шаг < 8 && (await ближе.isEnabled()); шаг++) {
    await ближе.click()
    const рамки = await линия
      .locator('text[data-normal]')
      .evaluateAll((els) =>
        els.map((e) => e.getBoundingClientRect()).map((r) => [r.left, r.right] as const),
      )
    подписей += рамки.length
    рамки.sort((a, b) => a[0] - b[0])
    for (let i = 1; i < рамки.length; i++)
      expect(рамки[i][0], `шаг ${шаг}: подпись ${i} налезает на соседнюю`).toBeGreaterThanOrEqual(
        рамки[i - 1][1] - 0.5,
      )
  }
  expect(подписей, 'подписи вообще появлялись').toBeGreaterThan(0)
})
