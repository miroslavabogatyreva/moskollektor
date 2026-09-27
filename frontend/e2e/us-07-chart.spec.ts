// US-07. Прочитать график показаний — docs/user-stories.md, приёмка Ф-91.
// Названия test() — названия сценариев истории.
import { expect, test, type Page } from '@playwright/test'

interface Объект {
  section_id: number
  current_risk: { as_of: string } | null
}
interface Показание {
  read_time: string
  channel_id: number
  value_text: string | null
}

// Дата по Москве — так её показывают поля «С даты» и «По дату».
const мск = (d: Date) => d.toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' })

async function первыйУчасток(page: Page): Promise<Объект> {
  const риски = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    risk_rank: number
  }[]
  const id = риски.find((r) => r.risk_rank === 1)!.section_id
  return (await (await page.request.get(`/api/objects/${id}`)).json()) as Объект
}

const показания = (page: Page) => page.locator('section', { hasText: 'Показания датчиков' })

test('US-07 сц. 1: ряд за окно расчёта', async ({ page }) => {
  const о = await первыйУчасток(page)
  expect(о.current_risk, 'у участка есть прогноз').toBeTruthy()
  const срез = new Date(о.current_risk!.as_of)
  const начало = new Date(срез)
  начало.setDate(начало.getDate() - 6)

  await page.goto(`/objects/${о.section_id}`)
  const блок = показания(page)
  await expect(блок.getByLabel('С даты'), 'окно начинается за неделю до среза').toHaveValue(
    мск(начало),
  )
  await expect(блок.getByLabel('По дату'), 'окно кончается срезом расчёта').toHaveValue(мск(срез))
  await expect(блок).toContainText(
    `Окно расчёта: 7 суток до среза ${срез.toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })}`,
  )
  await expect(блок.locator('[data-channel-chart]').first()).toBeVisible()
})

test('US-07 сц. 2: оси подписаны', async ({ page }) => {
  const о = await первыйУчасток(page)
  await page.goto(`/objects/${о.section_id}`)
  const графики = показания(page).locator('[data-channel-chart]:has(svg)')
  await expect(графики.first()).toBeVisible()
  const n = await графики.count()
  for (let i = 0; i < n; i++) {
    const г = графики.nth(i)
    const имя = await г.locator('div').first().innerText()
    await expect(
      г.locator('[data-axis="time"] text').first(),
      `${имя}: у оси времени даты`,
    ).toHaveText(/\d\d\.\d\d\.\d{4}/)
    await expect(
      г.locator('[data-axis="value"]'),
      `${имя}: у оси значений единица или названия состояний`,
    ).toHaveText(/°C|единица|Состояния:\s*\S+/)
  }
})

test('US-07 сц. 3: потеря связи отмечена', async ({ page }) => {
  // Участок и день, где был эпизод «Неисправен», берём у таблицы отказов по каналам.
  const о = await первыйУчасток(page)
  const { items } = (await (
    await page.request.get(`/api/objects/${о.section_id}/channels?limit=1000`)
  ).json()) as { items: { channel_id: number; last_fault_at: string | null }[] }
  const канал = items.find((к) => к.last_fault_at)
  test.skip(!канал, 'у каналов участка не было отказов')
  const день = мск(new Date(канал!.last_fault_at!))

  await page.goto(`/objects/${о.section_id}`)
  const блок = показания(page)
  await блок.getByLabel('С даты').fill(день)
  await блок.getByLabel('По дату').fill(день)
  const график = блок.locator(`[data-channel-chart="${канал!.channel_id}"]`)
  const метки = график.locator('[data-episode-start]')
  await expect(метки.first(), 'эпизод отмечен на графике').toBeAttached()

  const журнал = (
    (await (
      await page.request.get(`/api/objects/${о.section_id}/readings?from=${день}&to=${день}`)
    ).json()) as Показание[]
  ).filter((r) => r.channel_id === канал!.channel_id)
  for (const начало of await метки.evaluateAll((els) =>
    els.map((e) => e.getAttribute('data-episode-start')!),
  )) {
    const i = журнал.findIndex((r) => r.read_time === начало)
    expect(i, `начало ${начало} — запись журнала СМВУ`).toBeGreaterThanOrEqual(0)
    expect(журнал[i].value_text).toBe('Неисправен')
    expect(журнал[i - 1]?.value_text, 'до начала канал писал другое').not.toBe('Неисправен')
  }
  await expect(график.locator('[data-episode-label]').first()).toContainText('Потеря связи')
})
