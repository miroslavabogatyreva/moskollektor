// US-18. План профилактики на неделю (руководитель) — docs/user-stories.md,
// приёмка М-16, Ф-94. Названия test() — названия сценариев истории.
//
// Роли «руководитель подразделения» среди демо-учёток нет. Ближе всех к ней
// диспетчер района: disp2 видит район А — коллекторы 5 и 7 (db/seed/rbac.sql),
// поэтому заявки у него только своего района, а не всего парка.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' } })

interface Строка {
  id: number
  due_at: string
  status: string
}

async function заявки(
  request: APIRequestContext,
  qs = '',
): Promise<{ total: number; items: Строка[] }> {
  const r = await request.get(`/api/orders?limit=1000${qs}`)
  expect(r.status(), `GET /api/orders${qs}`).toBe(200)
  return r.json()
}

// Дата по Москве в формате поля <input type="date">.
const мск = (ms: number) => new Date(ms).toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' })
const СУТКИ = 24 * 3600 * 1000

const строки = (page: Page) => page.locator('#orders-panel-orders table tbody tr')

test('US-18 сц. 1: Заявки по сроку', async ({ page, request }) => {
  const { items } = await заявки(request)
  expect(items.length, 'у района есть заявки').toBeGreaterThan(0)
  const ближайший = Math.min(...items.map((з) => Date.parse(з.due_at)))
  const первая = items.find((з) => Date.parse(з.due_at) === ближайший)!

  await page.goto('/orders')
  await expect(
    page.getByRole('columnheader', { name: /Срок/ }),
    'список отсортирован по сроку, и это видно в заголовке',
  ).toHaveAttribute('aria-sort', 'ascending')
  await expect(строки(page).first().locator('td').first()).toHaveText(String(первая.id))
})

test('US-18 сц. 2: Отбор по неделе', async ({ page, request }) => {
  await page.goto('/orders')
  await expect(строки(page).first()).toBeVisible()

  // Кнопка «7 дней вперёд» ставит период от сегодня на неделю.
  await page.getByRole('button', { name: '7 дней вперёд' }).click()
  const сегодня = Date.now()
  await expect(page.getByLabel('Срок с')).toHaveValue(мск(сегодня))
  await expect(page.getByLabel('Срок по')).toHaveValue(мск(сегодня + 6 * СУТКИ))
  const неделя = await заявки(
    request,
    `&due_from=${мск(сегодня)}&due_to=${мск(сегодня + 6 * СУТКИ)}`,
  )
  await expect(page.getByTestId('orders-count')).toHaveText(`заявок в периоде: ${неделя.total}`)
  await expect(строки(page)).toHaveCount(неделя.total)

  // На стенде срезы данных в прошлом, и на неделю от сегодня сроков может не быть.
  // Поэтому второй раз — неделя, начинающаяся с ближайшего срока района: в ней
  // заявки есть наверняка, и число строк сверяем с API тем же периодом.
  const { items } = await заявки(request)
  const с = мск(Math.min(...items.map((з) => Date.parse(з.due_at))))
  const по = мск(Date.parse(`${с}T12:00:00+03:00`) + 6 * СУТКИ)
  await page.getByLabel('Срок с').fill(с)
  await page.getByLabel('Срок по').fill(по)
  const период = await заявки(request, `&due_from=${с}&due_to=${по}`)
  expect(период.total, 'в неделе от ближайшего срока есть заявки').toBeGreaterThan(0)
  expect(период.total, 'отбор по сроку сужает список').toBeLessThan(items.length)
  await expect(page.getByTestId('orders-count')).toHaveText(`заявок в периоде: ${период.total}`)
  await expect(строки(page)).toHaveCount(период.total)
  for (const з of период.items) {
    const день = мск(Date.parse(з.due_at))
    expect(день >= с && день <= по, `заявка ${з.id}: срок ${день} в периоде ${с}…${по}`).toBe(true)
  }
})

test('US-18 сц. 3: Просроченное видно словом', async ({ page, request }) => {
  const { items } = await заявки(request)
  // Просрочку экран меряет от среза расчёта (dashboard/api.ts, моментРасчёта), не от часов:
  // на стенде проигрывается архив, и срок заявки сравнивается с моментом данных.
  const { as_of } = (await (await request.get('/api/data-status')).json()) as {
    as_of: string | null
  }
  const сейчас = as_of ? Date.parse(as_of) : Date.now()
  const просрочена = (з: Строка) =>
    Date.parse(з.due_at) < сейчас && !['COMPLETED', 'CANCELLED'].includes(з.status)
  const сколько = items.filter(просрочена).length
  // Без просроченной заявки слово проверить не на чем: срез проигрывания мог ещё
  // не дойти до ближайшего срока. Пропуск с числом, а не молчаливый зелёный.
  test.skip(сколько === 0, `на срезе ${as_of} просроченных заявок 0 — слово проверить не на чем`)

  await page.goto('/orders')
  await expect(строки(page)).toHaveCount(Math.min(items.length, 200))
  // Первая страница списка — первые 200 по сроку, как в ответе API.
  for (const з of items.slice(0, 200)) {
    const строка = строки(page).filter({
      has: page.getByRole('cell', { name: String(з.id), exact: true }),
    })
    if (просрочена(з)) await expect(строка, `заявка ${з.id}`).toContainText('просрочено')
    else await expect(строка, `заявка ${з.id}`).not.toContainText('просрочено')
  }
})
