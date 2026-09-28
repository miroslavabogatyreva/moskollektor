// Отбор заявок по статусу и приоритету на экране «Заявки» (28.09.2026): выпадающие
// списки «Статус» и «Приоритет», отбор живёт в адресе ?status=&priority=, плитка
// «Заявки в работе» на главной ведёт на ?status=active. Против стенда, только GET.
//
// E2E_BUNDLE=dist — своя сборка вместо бандла стенда (helpers/sensor-mock.ts). Пока
// параметров status и priority на стенде нет, в этом режиме ответ /api/orders
// подменяется: тест берёт настоящий список стенда и отбирает его сам по тем же
// правилам, что backend/app/api/orders.py. Без E2E_BUNDLE — живой ответ сервера.
import { expect, test, type Page } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' } })

type Заявка = { id: number; status: string; priority_code: string }
const АКТИВНЫЕ = ['OPEN', 'IN_PROCESS']
const подходит = (o: Заявка, status: string | null, priority: string | null) =>
  (!status || (status === 'active' ? АКТИВНЫЕ.includes(o.status) : o.status === status)) &&
  (!priority || o.priority_code === priority)

// Все заявки disp2 одним запросом — из них тест считает ожидаемое число любого отбора.
async function всеЗаявки(page: Page): Promise<Заявка[]> {
  const r = await page.request.get('/api/orders?limit=1000')
  expect(r.status()).toBe(200)
  const { items, total } = (await r.json()) as { items: Заявка[]; total: number }
  expect(items.length, 'все заявки disp2 влезли в одну страницу').toBe(total)
  return items
}

// Запросы экрана к /api/orders; в режиме своей сборки — с подменой ответа.
async function слушатьЗаявки(page: Page, все: Заявка[]): Promise<URL[]> {
  const запросы: URL[] = []
  await page.route('**/api/orders?**', (route) => {
    const url = new URL(route.request().url())
    запросы.push(url)
    if (!process.env.E2E_BUNDLE) return route.continue()
    const p = url.searchParams
    const отобрано = все.filter((o) => подходит(o, p.get('status'), p.get('priority')))
    const offset = Number(p.get('offset') ?? 0)
    return route.fulfill({
      json: {
        schema_version: 'orders.v1',
        total: отобрано.length,
        items: отобрано.slice(offset, offset + 200),
      },
    })
  })
  await page.route('**/api/orders', (route) => {
    запросы.push(new URL(route.request().url()))
    return route.continue()
  })
  return запросы
}

test.beforeEach(({ page }) => свойБандл(page))

test('отбор по статусу и приоритету: число с сервера, отбор в адресе', async ({ page }) => {
  const все = await всеЗаявки(page)
  const запросы = await слушатьЗаявки(page, все)
  const число = page.getByTestId('orders-count')
  const строки = page.locator('#orders-panel-orders table tbody tr')
  const статус = page.getByLabel('Статус')
  const приоритет = page.getByLabel('Приоритет')
  const сколько = (s: string | null, p: string | null) =>
    все.filter((o) => подходит(o, s, p)).length

  // Ссылка с приоритетом открывает тот же отбор.
  await page.goto('/orders?priority=3')
  await expect(приоритет).toHaveValue('3')
  await expect(число).toHaveText(`найдено заявок: ${сколько(null, '3')}`)
  await expect(строки).toHaveCount(Math.min(сколько(null, '3'), 200))
  for (const бейдж of await строки.locator('td:last-child').allTextContents())
    expect(бейдж).toContain('Средний')
  expect(запросы.at(-1)!.searchParams.get('priority')).toBe('3')

  // Статус добавляется к приоритету, оба в адресе и в запросе.
  await статус.selectOption('COMPLETED')
  await expect(page).toHaveURL(/[?&]status=COMPLETED/)
  await expect(page).toHaveURL(/[?&]priority=3/)
  await expect(число).toHaveText(`найдено заявок: ${сколько('COMPLETED', '3')}`)
  await expect.poll(() => запросы.at(-1)!.searchParams.get('status')).toBe('COMPLETED')

  // «Все» в обоих списках — без отбора, адрес чистый.
  await статус.selectOption('')
  await приоритет.selectOption('')
  await expect(page).toHaveURL(/\/orders$/)
  await expect(число).toHaveText(`заявок: ${все.length}`)
})

test('плитка «Заявки в работе» ведёт на открытые и в работе', async ({ page }) => {
  const все = await всеЗаявки(page)
  await слушатьЗаявки(page, все)
  await page.goto('/map')
  await page.getByRole('link', { name: /^Заявки в работе/ }).click()
  await expect(page).toHaveURL(/\/orders\?status=active$/)
  await expect(page.getByLabel('Статус')).toHaveValue('active')
  const активных = все.filter((o) => подходит(o, 'active', null)).length
  await expect(page.getByTestId('orders-count')).toHaveText(`найдено заявок: ${активных}`)
})

test('чужой статус в адресе — как «все», в запрос не уходит', async ({ page }) => {
  const все = await всеЗаявки(page)
  const запросы = await слушатьЗаявки(page, все)
  await page.goto('/orders?status=bogus&priority=9')
  await expect(page.getByTestId('orders-count')).toHaveText(`заявок: ${все.length}`)
  await expect(page.getByLabel('Статус')).toHaveValue('')
  expect(запросы.length, 'экран запросил заявки').toBeGreaterThan(0)
  for (const u of запросы) {
    expect(u.searchParams.has('status')).toBe(false)
    expect(u.searchParams.has('priority')).toBe(false)
  }
})
