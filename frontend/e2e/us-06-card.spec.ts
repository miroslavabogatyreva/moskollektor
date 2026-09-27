// US-06. Проверить прогноз в карточке участка — docs/user-stories.md, приёмка
// М-08, Ф-70, Ф-73. Названия test() — названия сценариев истории.
//
// Участок берём первый на дашборде: у него прогноз есть всегда, а сц. 3 и 4
// ему не пусты (объяснение модели, последняя запись до момента расчёта).
import { expect, test, type Page } from '@playwright/test'

interface Объект {
  section_id: number
  smvu_key: string
  last_reading_at: string | null
  current_risk: { probability: number; horizon_h: number; direction: string } | null
  recent_forecasts: { forecast_id: number }[]
}

async function участок(page: Page): Promise<Объект> {
  const риски = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    risk_rank: number
  }[]
  const первый = риски.find((r) => r.risk_rank === 1)!
  return (await (await page.request.get(`/api/objects/${первый.section_id}`)).json()) as Объект
}

// «847:1» → «Коллектор 847, пикет 1» — тем же словом, что на дашборде и в заявках.
const имя = (smvu_key: string) => {
  const [к, п] = smvu_key.split(':')
  return `Коллектор ${к}, пикет ${п}`
}

// «27.09.2026, 19:44:30» → момент. Запись и расчёт бывают в одни сутки
// (эмулятор СМВУ пишет вживую), поэтому сравниваем с точностью до секунды.
const момент = (ру: string) => {
  const [д, м, г, ч, мин, с] = ру
    .match(/(\d\d)\.(\d\d)\.(\d{4}),\s*(\d\d?):(\d\d):(\d\d)/)!
    .slice(1)
    .map(Number)
  return new Date(г, м - 1, д, ч, мин, с).getTime()
}

test('US-06 сц. 1: четыре атрибута прогноза', async ({ page }) => {
  const о = await участок(page)
  await page.goto(`/objects/${о.section_id}`)
  // Из карточки участка — в карточку прогноза, строкой «Последние прогнозы».
  await page
    .locator('section', { hasText: 'Последние прогнозы' })
    .locator('tbody tr')
    .first()
    .click()
  await expect(page).toHaveURL(/\/forecasts\/\d+$/)
  const id = Number(page.url().match(/\/forecasts\/(\d+)$/)![1])
  const п = (await (await page.request.get(`/api/forecasts/${id}`)).json()) as {
    section_id: number
    probability: number
    horizon_h: number
    direction: string
  }
  expect(п.section_id).toBe(о.section_id)

  const main = page.locator('main')
  await expect(main, 'вероятность').toContainText(`вероятность ${п.probability.toFixed(4)}`)
  await expect(main, 'горизонт в часах').toContainText(`горизонт ${п.horizon_h} ч`)
  await expect(main, 'класс инцидента').toContainText('Отказ датчика')
  expect(п.direction).toBe('sensor_failure')
  await expect(main, 'участок — коллектор и пикет').toContainText(имя(о.smvu_key))
})

test('US-06 сц. 2: какой канал под угрозой', async ({ page }) => {
  const о = await участок(page)
  const { items } = (await (
    await page.request.get(`/api/objects/${о.section_id}/channels?limit=1000`)
  ).json()) as { items: { name: string; sensor_kind: string }[] }
  expect(items.length, 'у участка есть каналы').toBeGreaterThan(0)

  await page.goto(`/objects/${о.section_id}`)
  const отказы = page.locator('section', { hasText: 'Отказы по каналам' }).locator('tbody tr')
  await expect(отказы).toHaveCount(items.length)
  for (const к of items)
    await expect(
      отказы.filter({ hasText: к.name }).first(),
      `канал «${к.name}» назван с типом датчика`,
    ).toContainText(к.sensor_kind)
})

test('US-06 сц. 3: причина риска написана словами', async ({ page }) => {
  const о = await участок(page)
  await page.goto(`/objects/${о.section_id}`)
  const блок = page.getByText('Почему такой риск', { exact: false }).locator('..')
  await expect(блок).toBeVisible()
  const строки = (await блок.locator('p').allInnerTexts()).filter((т) => т.trim())
  expect(строки.length, 'названы факторы риска').toBeGreaterThan(0)
  for (const т of строки) {
    expect(т, 'фактор назван со значением').toMatch(/\d/)
    expect(т, 'фактор — это слова, а не одна вероятность').toMatch(/[а-я]{4,}.*[а-я]{4,}/)
  }
})

test('US-06 сц. 4: видно, что данные участка устарели', async ({ page }) => {
  // Дано: последняя запись участка раньше момента расчёта. Эмулятор СМВУ пишет
  // вживую, и у части участков запись свежее расчёта — берём первый по рангу,
  // у которого это не так.
  const риски = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    risk_rank: number
  }[]
  let о: (Объект & { current_risk: { computed_at: string } | null }) | undefined
  for (const r of риски.sort((a, b) => a.risk_rank - b.risk_rank).slice(0, 30)) {
    const x = (await (await page.request.get(`/api/objects/${r.section_id}`)).json()) as typeof о
    if (
      x?.last_reading_at &&
      x.current_risk &&
      Date.parse(x.last_reading_at) < Date.parse(x.current_risk.computed_at)
    ) {
      о = x
      break
    }
  }
  expect(о, 'среди первых 30 участков есть участок с записью раньше расчёта').toBeTruthy()
  await page.goto(`/objects/${о!.section_id}`)
  const риск = page.locator('section', { hasText: 'Уровень риска' })
  const запись = await риск.getByText(/^Последняя запись участка — /).innerText()
  const расчёт = await риск.getByText(/^Расчёт от /).innerText()
  expect(момент(запись), `${запись} раньше, чем ${расчёт}`).toBeLessThan(момент(расчёт))
})
