// US-01. Начало смены: увидеть, где сейчас риск — docs/user-stories.md, приёмка
// М-04, Ф-01, Ф-57. Названия test() — названия сценариев истории.
import { expect, test, type Page } from '@playwright/test'

interface Risk {
  section_id: number
  probability: number
  risk_rank: number
  is_stale: boolean
  risk_class: 'high' | 'normal' | null
}

const строки = (page: Page) => page.locator('main table tbody tr')

// Номер участка стоит в столбце «Объект» серым после точки: «Коллектор 889, пикет 1 · 2477».
async function участокСтроки(page: Page, i: number): Promise<number> {
  const текст = await строки(page).nth(i).locator('td').nth(1).innerText()
  return Number(текст.match(/·\s*(\d+)\s*$/)?.[1])
}

test('US-01 сц. 1: самый рискованный участок наверху', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as Risk[]
  expect(риски.length, 'на дашборде больше одного участка').toBeGreaterThan(1)
  const максимум = Math.max(...риски.map((r) => r.probability))

  const начало = Date.now()
  await page.goto('/dashboard')
  await expect(строки(page).first()).toBeVisible()
  const готово = Date.now() - начало

  // Первые 50 строк — это больше, чем помещается на первом экране.
  const вероятности = (await строки(page).locator('td:nth-child(4)').allInnerTexts())
    .slice(0, 50)
    .map(Number)
  for (let i = 1; i < вероятности.length; i++) {
    expect(вероятности[i], `строка ${i + 1} не выше строки ${i}`).toBeLessThanOrEqual(
      вероятности[i - 1],
    )
  }
  const первый = риски.find((r) => r.risk_rank === 1)!
  expect(первый.probability).toBe(максимум)
  expect(await участокСтроки(page, 0), 'первая строка — участок ранга 1 из /api/risks').toBe(
    первый.section_id,
  )

  const высота = page.viewportSize()!.height
  let наЭкране = 0
  for (const box of await строки(page).evaluateAll((els) =>
    els.slice(0, 100).map((e) => e.getBoundingClientRect().bottom),
  ))
    if (box <= высота) наЭкране++
  test.info().annotations.push({
    type: 'замер',
    description: `список готов за ${готово} мс; строк на первом экране без прокрутки: ${наЭкране}`,
  })
})

test('US-01 сц. 2: уровень риска читается без цифр', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(строки(page).first()).toBeVisible()

  // Цвет полоски строки для каждого уровня, который есть на экране.
  const цветСтроки: Record<string, string> = {}
  for (const слово of ['высокий риск', 'низкий риск']) {
    const строка = строки(page).filter({ hasText: слово }).first()
    if ((await строка.count()) === 0) continue
    цветСтроки[слово] = await строка.evaluate((e) => getComputedStyle(e).borderLeftColor)
  }
  expect(Object.keys(цветСтроки), 'на экране есть высокий и низкий риск').toEqual([
    'высокий риск',
    'низкий риск',
  ])
  // У каждой строки есть слово уровня.
  const словаРиска = await строки(page).locator('td:nth-child(3)').allInnerTexts()
  for (const [i, т] of словаРиска.entries())
    expect(т, `строка ${i + 1}`).toMatch(/высокий риск|низкий риск|класса нет/)

  // Цвет того же уровня в легенде схемы коллектора — обводка значка.
  await page.goto('/map')
  for (const [слово, цвет] of Object.entries(цветСтроки)) {
    const значок = page
      .locator('main span', { hasText: new RegExp(`^${слово}$`) })
      .locator('svg > *')
      .first()
    await expect(значок).toBeAttached()
    const обводка = await значок.evaluate((e) => getComputedStyle(e).stroke)
    expect(цвет, `цвет строки «${слово}» равен цвету в легенде схемы`).toBe(обводка)
  }
})

test('US-01 сц. 3: при равном риске важнее газ', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as Risk[]
  // Группы участков с одинаковой вероятностью — на них порядок решает система.
  const группы = new Map<number, Risk[]>()
  for (const r of риски) группы.set(r.probability, [...(группы.get(r.probability) ?? []), r])
  const равные = [...группы.values()].filter((g) => g.length > 1)
  test.skip(равные.length === 0, 'на стенде нет участков с равной вероятностью')

  const важность = (s: string) =>
    ['Газовая охрана', 'Пожарная охрана', 'Охранная подсистема'].indexOf(s) + 1 || 4
  const системаУчастка = async (id: number) => {
    const r = await page.request.get(`/api/objects/${id}/channels?limit=1000`)
    const { items } = (await r.json()) as { items: { system_kind: string }[] }
    return Math.min(4, ...items.map((c) => важность(c.system_kind)))
  }
  // Стенд под лимитом nginx 30 запросов в секунду: берём до 40 участков.
  let пар = 0
  let сГазом = 0
  for (const группа of равные) {
    if (пар >= 40) break
    const сВажностью = []
    for (const r of группа.slice(0, 5))
      сВажностью.push({ ...r, в: await системаУчастка(r.section_id) })
    пар += сВажностью.length
    const поРангу = [...сВажностью].sort((a, b) => a.risk_rank - b.risk_rank)
    for (let i = 1; i < поРангу.length; i++)
      expect(
        поРангу[i].в,
        `вероятность ${поРангу[i].probability}: участок ${поРангу[i].section_id} (ранг ${поРангу[i].risk_rank}) не важнее участка ${поРангу[i - 1].section_id} (ранг ${поРангу[i - 1].risk_rank})`,
      ).toBeGreaterThanOrEqual(поРангу[i - 1].в)
    if (сВажностью.some((r) => r.в === 1) && сВажностью.some((r) => r.в > 1)) сГазом++
  }
  test.info().annotations.push({
    type: 'замер',
    description: `групп с равной вероятностью: ${равные.length}; проверено участков: ${пар}; групп, где газ рядом с другой системой: ${сГазом}`,
  })
})

test('US-01 сц. 4: видно, что посчитаны не все участки', async ({ page }) => {
  const статус = (await (await page.request.get('/api/data-status')).json()) as {
    sections_scored: number
    sections_total: number
  }
  expect(статус.sections_total, 'M пришло в ответе API').toBeGreaterThan(0)
  await page.goto('/dashboard')
  await expect(
    page.locator('article', { hasText: 'Участков в расчёте' }),
    'N и M на экране совпадают с GET /api/data-status',
  ).toContainText(`посчитано ${статус.sections_scored} из ${статус.sections_total}`)
})
