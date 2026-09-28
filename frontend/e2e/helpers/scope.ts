import { expect, type Page } from '@playwright/test'

// Область видимости роли на четырёх экранах (US-16 сц. 1, US-21 сц. 1): дашборд,
// схема, журнал прогнозов и заявки показывают только участки, которые сервер
// отдаёт этой роли в GET /api/risks. Страница уже открыта под нужным логином
// (test.use({ extraHTTPHeaders })).

interface Участок {
  section_id: number
  smvu_key: string
  collector: number
}

export async function видимые(page: Page): Promise<Set<number>> {
  const риски = (await (await page.request.get('/api/risks')).json()) as { section_id: number }[]
  return new Set(риски.map((r) => r.section_id))
}

export async function справочник(page: Page): Promise<Участок[]> {
  return (await (await page.request.get('/data/sections.json')).json()) as Участок[]
}

// Номер участка в строке дашборда — серым после точки: «Коллектор 889, пикет 1 · 2477».
export async function проверитьЭкраны(page: Page, свои: Set<number>) {
  const участки = await справочник(page)
  const поКлючу = new Map(участки.map((у) => [у.smvu_key, у.section_id]))
  const поИмени = (имя: string) => {
    const m = имя.match(/Коллектор (\d+), пикет (\d+)/)
    return m ? поКлючу.get(`${m[1]}:${m[2]}`) : undefined
  }

  // Дашборд: строк столько же, сколько участков роли, и все свои. Весь список —
  // режим «по участкам» с ?level=all (по умолчанию экран по датчикам, 28.09.2026).
  await page.goto('/dashboard?view=sections&level=all')
  const строки = page.locator('main table tbody tr')
  await expect(строки).toHaveCount(свои.size, { timeout: 30_000 })
  const номера = (await строки.locator('td:nth-child(2)').allInnerTexts()).map((т) =>
    Number(т.match(/·\s*(\d+)\s*$/)?.[1]),
  )
  expect(
    номера.filter((n) => !свои.has(n)),
    'на дашборде чужих участков нет',
  ).toEqual([])

  // Схема: в списке только коллекторы своих участков, метки только свои. Метки участков
  // и /api/risks — на оси участков (?axis=sections): по умолчанию главная — ось датчиков.
  const рискиСхемы = page.waitForResponse((r) => r.url().endsWith('/api/risks'))
  await page.goto('/map?axis=sections')
  await рискиСхемы
  const своиКоллекторы = new Set(
    участки.filter((у) => свои.has(у.section_id)).map((у) => String(у.collector)),
  )
  const выбор = page.locator('select').first()
  await expect(выбор.locator('option')).toHaveCount(своиКоллекторы.size)
  for (const к of await выбор
    .locator('option')
    .evaluateAll((оп) => оп.map((о) => (о as HTMLOptionElement).value))) {
    expect(своиКоллекторы.has(к), `коллектор ${к} на схеме свой`).toBe(true)
    await выбор.selectOption(к)
    const метки = await page
      .locator('main svg[role="img"] g[data-section-id]')
      .evaluateAll((гг) => гг.map((г) => Number(г.getAttribute('data-section-id'))))
    expect(
      метки.filter((n) => !свои.has(n)),
      `на оси коллектора ${к} чужих меток нет`,
    ).toEqual([])
  }

  // Журнал прогнозов: первая страница — только свои участки.
  await page.goto('/log')
  const журнал = page.locator('main table tbody tr')
  await expect(журнал.first()).toBeVisible({ timeout: 30_000 })
  const вЖурнале = (await журнал.locator('td:nth-child(2)').allInnerTexts()).map(поИмени)
  expect(
    вЖурнале.filter((n) => n === undefined || !свои.has(n)),
    'в журнале чужих нет',
  ).toEqual([])

  // Заявки: все строки — свои участки.
  const заявки = (await (await page.request.get('/api/orders?limit=1000')).json()) as {
    items: { smvu_key: string }[]
  }
  expect(
    заявки.items
      .map((з) => поКлючу.get(з.smvu_key))
      .filter((n) => n === undefined || !свои.has(n!)),
    'в заявках чужих нет',
  ).toEqual([])
}
