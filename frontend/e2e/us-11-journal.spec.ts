// US-11. Найти прогноз в журнале — docs/user-stories.md, приёмка М-06, М-16.
// Отбор по участку делает сервер (GET /api/forecasts?section_id=), а не браузер
// в пределах страницы: иначе число строк не сходится с API, как только прогнозов
// участка больше, чем влезло на страницу. Отбор живёт в адресе /log?…, поэтому
// «назад» из карточки прогноза возвращает тот же отбор.
import { expect, test, type Page } from '@playwright/test'

interface Row {
  forecast_id: number
  section_id: number
  computed_at: string
}

const строки = (page: Page) => page.locator('main table tbody tr')
// Первая страница журнала на стенде приходит за 3–5 с (goto около 3 с плюс журнал за
// день около 1,5 с, рядом сводка исходов US-20): умолчание expect 5 с — впритык,
// 27.09.2026 три теста упали на нём. Сколько она идёт на самом деле, пишет замер сц. 1.
const ПЕРВАЯ_СТРАНИЦА = { timeout: 15_000 }
const день = (iso: string) => new Date(iso).toLocaleDateString('sv-SE')
const днейНазад = (n: number) => день(new Date(Date.now() - n * 86_400_000).toISOString())

async function свежий(page: Page): Promise<Row> {
  const { items } = (await (await page.request.get('/api/forecasts?limit=1')).json()) as {
    items: Row[]
  }
  return items[0]
}

async function отобрать(page: Page, с: string, по: string, участок: number) {
  await page.getByLabel('С даты').fill(с)
  await page.getByLabel('По дату').fill(по)
  const загрузка = page.waitForResponse(
    (r) => r.url().includes('/api/forecasts?') && r.url().includes(`section_id=${участок}`),
  )
  await page.getByLabel('Участок').fill(String(участок))
  await page.getByLabel('Участок').press('Enter')
  await загрузка
}

test('US-11 сц. 1: отбор по периоду и участку', async ({ page }) => {
  const прогноз = await свежий(page)
  const с = днейНазад(90)
  const по = день(прогноз.computed_at)
  const { total } = (await (
    await page.request.get(
      `/api/forecasts?from=${с}&to=${по}&section_id=${прогноз.section_id}&limit=1`,
    )
  ).json()) as { total: number }
  const всего = (await (
    await page.request.get(`/api/forecasts?from=${с}&to=${по}&limit=1`)
  ).json()) as { total: number }
  expect(total, 'у участка есть прогнозы за период').toBeGreaterThan(0)
  expect(total, 'участок — не весь журнал').toBeLessThan(всего.total)

  const начало = Date.now()
  await page.goto('/log')
  await expect(строки(page).first()).toBeVisible(ПЕРВАЯ_СТРАНИЦА)
  const перваяСтраница = Date.now() - начало

  await отобрать(page, с, по, прогноз.section_id)
  await expect(page.getByTestId('log-total')).toHaveText(`Найдено: ${total}`)
  const участки = await строки(page).evaluateAll((els) =>
    els.map((e) => Number(e.getAttribute('data-section-id'))),
  )
  expect(участки.length).toBe(Math.min(total, 200))
  expect(new Set(участки), 'в журнале только выбранный участок').toEqual(
    new Set([прогноз.section_id]),
  )
  test.info().annotations.push({
    type: 'замер',
    description: `первая страница журнала за ${перваяСтраница} мс; прогнозов участка ${прогноз.section_id} за ${с}…${по}: ${total} из ${всего.total}`,
  })
})

test('US-11 сц. 2: период включает последний день', async ({ page }) => {
  const прогноз = await свежий(page)
  const по = день(прогноз.computed_at)
  await page.goto('/log')
  await отобрать(page, по, по, прогноз.section_id)
  await expect(
    page.locator(`main table tbody tr[data-forecast-id="${прогноз.forecast_id}"]`),
    `прогноз ${прогноз.computed_at} в журнале «по ${по}»`,
  ).toBeVisible()
})

test('US-11 сц. 3: видно, чем кончилось', async ({ page }) => {
  await page.goto('/log')
  await expect(строки(page).first()).toBeVisible(ПЕРВАЯ_СТРАНИЦА)
  const заголовки = await page.locator('main table thead th').allInnerTexts()
  expect(заголовки.map((т) => т.replace(/[↑↓]/g, '').trim().toLowerCase())).toEqual(
    expect.arrayContaining(['решение', 'исход']),
  )
  // У каждой строки решение и исход написаны словами — пустых ячеек нет.
  const решения = await строки(page).locator('td:nth-child(6)').allInnerTexts()
  const исходы = await строки(page).getByTestId('outcome-cell').allInnerTexts()
  expect(решения.length).toBeGreaterThan(0)
  expect(исходы.length).toBe(решения.length)
  for (const [i, т] of [...решения, ...исходы].entries())
    expect(т.trim(), `ячейка ${i + 1}`).not.toBe('')
})

test('US-11 сц. 4: возврат не сбрасывает отбор', async ({ page }) => {
  const прогноз = await свежий(page)
  const с = днейНазад(90)
  const по = день(прогноз.computed_at)
  await page.goto('/log')
  await отобрать(page, с, по, прогноз.section_id)
  await expect(строки(page).first()).toBeVisible(ПЕРВАЯ_СТРАНИЦА)
  const доКарточки = await строки(page).count()

  await строки(page).first().click()
  await expect(page).toHaveURL(/\/forecasts\/\d+$/)
  await page.goBack()

  await expect(page).toHaveURL(/\/log\?/)
  await expect(page.getByLabel('С даты')).toHaveValue(с)
  await expect(page.getByLabel('По дату')).toHaveValue(по)
  await expect(page.getByLabel('Участок')).toHaveValue(String(прогноз.section_id))
  await expect(строки(page)).toHaveCount(доКарточки)
})
