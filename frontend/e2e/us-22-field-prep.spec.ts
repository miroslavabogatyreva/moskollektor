// US-22. Подготовиться к выезду (техник) — docs/user-stories.md, приёмка М-11, Ф-73.
// Названия test() — названия сценариев истории.
//
// Роль задана здесь, а не через E2E_LOGIN: история техника, и tech1 видит только
// свой комплекс. Заявку берём первую по сроку, у участка которой есть канал,
// хоть раз терявший связь, — иначе сц. 3 проверять не на чем.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'tech1' } })

interface Заявка {
  id: number
  object: {
    section_id: number
    collector: number
    picket: number
    criticality_code: string
    criticality_reason: string
  }
  work_type: { activity_type_code: string; activity_type_name: string }
  forecast: { probability: number; horizon_h: number }
}
interface Канал {
  channel_id: number
  name: string
  sensor_kind: string
  system_kind: string
  faults_cnt: number
}

async function заявкаСКаналом(request: APIRequestContext): Promise<{ з: Заявка; к: Канал }> {
  const список = (await (await request.get('/api/orders?limit=1000')).json()) as {
    items: { id: number }[]
  }
  expect(список.items.length, 'у техника есть заявки').toBeGreaterThan(0)
  for (const { id } of список.items) {
    const з = (await (await request.get(`/api/orders/${id}`)).json()) as Заявка
    const каналы = (await (
      await request.get(`/api/objects/${з.object.section_id}/channels?limit=1`)
    ).json()) as { items: Канал[] }
    const к = каналы.items[0]
    if (к && к.faults_cnt > 0) return { з, к }
  }
  throw new Error('ни у одной заявки техника нет канала, терявшего связь')
}

const поле = (page: Page, label: string) =>
  page.getByText(label, { exact: true }).locator('xpath=following-sibling::div')

test('US-22 сц. 1: Где', async ({ page, request }) => {
  const { з, к } = await заявкаСКаналом(request)
  await page.goto(`/orders/${з.id}`)
  await expect(поле(page, 'Объект')).toContainText(
    `Коллектор ${з.object.collector}, пикет ${з.object.picket}`,
  )
  // Канал — тот, что чаще других каналов участка терял связь (первый в
  // GET /api/objects/{id}/channels), и тип его датчика.
  const канал = поле(page, 'Канал')
  await expect(канал, 'канал назван').toContainText(к.name.trim())
  await expect(канал, 'тип датчика назван').toContainText(к.sensor_kind)
})

test('US-22 сц. 2: Что проверять', async ({ page, request }) => {
  const { з } = await заявкаСКаналом(request)
  expect(з.work_type.activity_type_code, 'вид работ — код справочника').toBeTruthy()
  await page.goto(`/orders/${з.id}`)
  const вид = поле(page, 'Вид работ')
  await expect(вид).toContainText(з.work_type.activity_type_name)
  // Показатель, по которому расчёт выбрал вид работ, — класс критичности участка
  // (order_rules.ВИД_РАБОТ) и его причина; рядом — вероятность, из-за которой заявка есть.
  await expect(вид, 'класс критичности назван').toContainText(
    `классу критичности участка «${з.object.criticality_code}»`,
  )
  await expect(вид, 'причина класса названа').toContainText(з.object.criticality_reason)
  await expect(вид, 'вероятность со значением').toContainText(
    з.forecast.probability.toFixed(3).replace('.', ','),
  )
})

test('US-22 сц. 3: История канала', async ({ page, request }) => {
  const { з, к } = await заявкаСКаналом(request)
  await page.goto(`/orders/${з.id}`)
  await page.getByRole('link', { name: /^История канала/ }).click()
  await expect(page).toHaveURL(
    new RegExp(`/objects/${з.object.section_id}\\?channel=${к.channel_id}$`),
  )
  const история = page.locator('section', { hasText: 'История канала' })
  await expect(история).toContainText(к.name.trim())
  const эпизоды = история.locator('li[data-episode-start]')
  // Столько же эпизодов, сколько отказов у канала в таблице «Отказы по каналам».
  await expect(эпизоды).toHaveCount(к.faults_cnt)
  for (const т of await эпизоды.allInnerTexts()) {
    expect(т, 'дата начала').toMatch(/\d\d\.\d\d\.\d{4} \d\d:\d\d/)
    expect(т, 'длительность или «не закрыт»').toMatch(/\d+,\d ч|не закрыт/)
  }
})

// Ссылка на канал в таблице «Отказы по каналам» открывает историю сверху карточки,
// а таблица внизу — без прокрутки к истории клик выглядел пустым (Слава, 28.09.2026).
test('US-22 сц. 3: клик по каналу в таблице показывает его историю', async ({ page, request }) => {
  const { з, к } = await заявкаСКаналом(request)
  await свойБандл(page)
  await page.goto(`/objects/${з.object.section_id}`)
  const таблица = page.locator('section', { hasText: 'Отказы по каналам' })
  const строка = таблица.getByRole('row', { name: к.name.trim() }).first()
  await строка.scrollIntoViewIfNeeded()
  await строка.click()
  const история = page.locator('section', { hasText: 'История канала' })
  await expect(история).toContainText(к.name.trim())
  await expect(история).toBeInViewport()
})
