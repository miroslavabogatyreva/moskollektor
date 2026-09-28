// Компоновка карточки участка (28.09.2026): две колонки от 1280 px, липкая строка
// разделов, свёрнутые паспорт и «Последние прогнозы». Замер до правки на 1440 px:
// участок 158 (60 каналов) — 11 194 px, участок 119 (54 канала) — 10 017 px.
// После — 3 596 и 3 292 px. Порог 6 000 px: старая карточка его не проходит вдвое,
// новая проходит с запасом на рост журнала.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

const УЧАСТОК = 158

test.beforeEach(({ page }) => свойБандл(page))
test.use({ viewport: { width: 1440, height: 900 } })

test('карточка на 1440 px короче 6 000 px', async ({ page }) => {
  await page.goto(`/objects/${УЧАСТОК}`)
  await expect(page.locator('[data-tour="readings"] [data-channel-chart]').first()).toBeVisible()
  const высота = await page.evaluate(() => document.documentElement.scrollHeight)
  expect(высота, `высота документа ${высота} px`).toBeLessThan(6000)
})

test('строка разделов ведёт к блокам и не уезжает при прокрутке', async ({ page }) => {
  await page.goto(`/objects/${УЧАСТОК}`)
  const разделы = page.getByRole('navigation', { name: 'Разделы карточки' })
  await expect(page.locator('[data-tour="readings"] [data-channel-chart]').first()).toBeVisible()
  for (const [подпись, заголовок] of [
    ['Показания', 'Показания датчиков'],
    ['События', 'Журнал технологических событий'],
    ['Отказы', 'Отказы по каналам'],
  ]) {
    await разделы.getByRole('link', { name: подпись }).click()
    const h = page.getByRole('heading', { name: заголовок, exact: true })
    await expect(h, `«${подпись}» ведёт к «${заголовок}»`).toBeInViewport()
    // «Отказы» стоят у верха страницы: прокрутка до них короче, чем путь строки
    // до верха окна, и строка ещё стоит на своём месте — поэтому y ≤ 0 не требуем
    // для него, а для нижних разделов требуем.
    const строка = (await разделы.boundingBox())!
    if (подпись !== 'Отказы') expect(строка.y, 'строка разделов прилипла к верху окна').toBe(0)
    expect((await h.boundingBox())!.y, 'заголовок не спрятан под строкой').toBeGreaterThanOrEqual(
      строка.y + строка.height,
    )
  }
  await expect(page, 'адрес без #: preact-router путь не меняет').toHaveURL(
    new RegExp(`/objects/${УЧАСТОК}$`),
  )
})

test('паспорт и прогнозы свёрнуты и раскрываются', async ({ page }) => {
  const о = (await (await page.request.get(`/api/objects/${УЧАСТОК}`)).json()) as {
    channels: unknown[]
  }
  await page.goto(`/objects/${УЧАСТОК}`)
  const паспорт = page.locator('section', { hasText: 'Паспорт: каналы участка' })
  const строки = паспорт.locator('tbody tr:not(:has(td[colspan]))')
  await expect(строки).toHaveCount(8)
  await паспорт.getByRole('button', { name: `Показать все каналы: ${о.channels.length}` }).click()
  await expect(строки).toHaveCount(о.channels.length)
  await паспорт.getByRole('button', { name: 'Свернуть' }).click()
  await expect(строки).toHaveCount(8)

  const прогнозы = page.locator('section', { hasText: 'Последние прогнозы' })
  await expect(прогнозы.locator('tbody tr')).toHaveCount(5)
  await прогнозы.getByRole('button', { name: /^Показать ещё \d+$/ }).click()
  expect(await прогнозы.locator('tbody tr').count()).toBeGreaterThan(5)
})

test('каналы без отказов свёрнуты под кнопку', async ({ page }) => {
  const { items: к } = (await (
    await page.request.get(`/api/objects/${УЧАСТОК}/channels?limit=200`)
  ).json()) as { items: { faults_cnt: number }[] }
  const сОтказами = к.filter((c) => c.faults_cnt > 0).length
  expect(к.length - сОтказами, 'у участка есть каналы без отказов').toBeGreaterThan(0)
  await page.goto(`/objects/${УЧАСТОК}`)
  const секция = page.locator('section', { hasText: 'Отказы по каналам' })
  const строки = секция.locator('tbody tr')
  await expect(строки).toHaveCount(сОтказами)
  await секция
    .getByRole('button', { name: `Показать каналы без отказов: ${к.length - сОтказами}` })
    .click()
  await expect(строки).toHaveCount(к.length)
})
