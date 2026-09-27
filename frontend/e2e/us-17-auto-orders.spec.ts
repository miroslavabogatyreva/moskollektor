// US-17. Заявка на профилактику появилась сама (руководитель) — docs/user-stories.md,
// приёмка М-09…М-13. Названия test() — дословно названия сценариев истории.
//
// Роли «руководитель» среди демо-учёток нет (GET /api/auth/info: dispatcher1, ods1,
// tech1, admin1), поэтому идём диспетчером ОДС — он видит заявки всего парка.
// Новый расчёт тест не запускает: на стенд без просьбы не пишем. Сценарий 1
// проверяет след уже прошедших расчётов — заявку, которую завёл расчёт.
import { expect, test, type APIRequestContext } from '@playwright/test'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })

interface OrderDetail {
  id: number
  source_system: string
  created_by: string | null
  reported_at: string
  due_at: string
  forecast: { forecast_id: number; as_of: string; horizon_h: number }
}

async function orderIds(request: APIRequestContext): Promise<number[]> {
  const r = await request.get('/api/orders?limit=1000')
  expect(r.status()).toBe(200)
  const body = (await r.json()) as { total: number; items: { id: number }[] }
  expect(body.total, 'на стенде нет ни одной заявки').toBeGreaterThan(0)
  return body.items.map((x) => x.id)
}

async function firstAutoOrder(request: APIRequestContext): Promise<OrderDetail> {
  for (const id of await orderIds(request)) {
    const d = (await (await request.get(`/api/orders/${id}`)).json()) as OrderDetail
    if (d.created_by === null) return d
  }
  throw new Error('ни одной заявки, заведённой расчётом (created_by = null)')
}

test('US-17 сц. 1: Заявка без участия человека', async ({ page, request }) => {
  const order = await firstAutoOrder(request)
  // Строка списка — кликабельная строка таблицы (rowLink), а не <a>.
  await page.goto('/orders')
  await page
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: String(order.id), exact: true }) })
    .click()
  await expect(page).toHaveURL(new RegExp(`/orders/${order.id}$`))
  await expect(page.getByText(/Завёл: расчёт,/)).toBeVisible()
})

test('US-17 сц. 2: В заявке четыре поля', async ({ page, request }) => {
  const order = await firstAutoOrder(request)
  await page.goto(`/orders/${order.id}`)
  for (const label of ['Объект', 'Вид работ', 'Срок выполнения', 'Обоснование']) {
    const value = page.getByText(label, { exact: true }).locator('xpath=following-sibling::div')
    await expect(value, `поле «${label}» пустое`).not.toBeEmpty()
  }
})

test('US-17 сц. 3: Срок раньше отказа', async ({ request }) => {
  // Каждая автозаявка, а не первая: срок, уехавший за горизонт у одной из трёхсот,
  // руководитель увидит так же, как у первой.
  const late: string[] = []
  for (const id of await orderIds(request)) {
    const d = (await (await request.get(`/api/orders/${id}`)).json()) as OrderDetail
    const horizonEnd = Date.parse(d.forecast.as_of) + d.forecast.horizon_h * 3600_000
    if (Date.parse(d.due_at) >= horizonEnd) {
      late.push(`№${id}: срок ${d.due_at}, горизонт до ${new Date(horizonEnd).toISOString()}`)
    }
  }
  expect(late, `сроки не раньше конца горизонта:\n${late.slice(0, 5).join('\n')}`).toEqual([])
})

test('US-17 сц. 4: Из заявки к прогнозу', async ({ page, request }) => {
  const order = await firstAutoOrder(request)
  await page.goto(`/orders/${order.id}`)
  await page.getByRole('link', { name: /^Прогноз, срез данных/ }).click()
  await expect(page).toHaveURL(new RegExp(`/forecasts/${order.forecast.forecast_id}$`))
})

test('US-17 сц. 5: Из прогноза к заявке', async ({ page, request }) => {
  const order = await firstAutoOrder(request)
  await page.goto(`/forecasts/${order.forecast.forecast_id}`)
  const block = page.locator('section', { hasText: 'Заявки по этому прогнозу' })
  await expect(block.locator(`a[href="/orders/${order.id}"]`)).toBeVisible()
})
