// US-21. Видеть только свой комплекс — docs/user-stories.md, приёмка НФ-44.
//
// Техник tech1 привязан к одному комплексу — коллектору 6 «объект Бета»
// (db/seed/rbac.sql), 79 участков на линиях СМВУ 15 и 798. «Диспетчер района,
// куда входит комплекс» — dispatcher1: район в выгрузке один (узел 5773),
// поэтому он видит весь парк, 3 173 участка.
//
// Прогон ничего не пишет: POST решения и исхода техник получает с 403,
// а тело запроса пустое — даже при сломанной проверке прав сервер ответил бы
// 422 и строку не завёл.
import { expect, test, type Page } from '@playwright/test'
import { проверитьЭкраны, справочник, видимые } from './helpers/scope'

const ТЕХНИК = 'tech1'
const КОМПЛЕКС = 6
const ДИСПЕТЧЕР_РАЙОНА = 'dispatcher1'

test.use({ extraHTTPHeaders: { 'X-User-Login': ТЕХНИК } })

async function какДиспетчер<T>(page: Page, путь: string): Promise<T> {
  const r = await page.request.get(путь, { headers: { 'X-User-Login': ДИСПЕТЧЕР_РАЙОНА } })
  return (await r.json()) as T
}

test('US-21 сц. 1: Только комплекс', async ({ page }) => {
  test.setTimeout(120_000)
  const свои = await видимые(page)
  const участки = await справочник(page)

  // Область техника — ровно один комплекс, и весь он.
  const комплекс = new Set(участки.filter((у) => у.collector === КОМПЛЕКС).map((у) => у.section_id))
  expect(комплекс.size, `в комплексе ${КОМПЛЕКС} есть участки`).toBeGreaterThan(0)
  expect([...свои].sort(), `техник видит ровно комплекс ${КОМПЛЕКС}`).toEqual([...комплекс].sort())

  // Меньше, чем у диспетчера района, и комплекс входит в его район.
  const района = new Set(
    (await какДиспетчер<{ section_id: number }[]>(page, '/api/risks')).map((r) => r.section_id),
  )
  expect(свои.size, 'участков у техника меньше, чем у диспетчера района').toBeLessThan(района.size)
  expect(
    [...свои].filter((id) => !района.has(id)),
    'комплекс входит в район диспетчера',
  ).toEqual([])

  // Дашборд (строк столько же, сколько участков комплекса), схема, журнал, заявки API.
  await проверитьЭкраны(page, свои)

  // Экран заявок: все строки — участки комплекса, и заявок меньше, чем у диспетчера.
  const поКлючу = new Map(участки.map((у) => [у.smvu_key, у.section_id]))
  const заявкиДиспетчера = await какДиспетчер<{ total: number }>(page, '/api/orders?limit=1')
  await page.goto('/orders')
  const строки = page.locator('main table tbody tr')
  await expect(строки.first()).toBeVisible({ timeout: 30_000 })
  const ключи = (await строки.locator('td:nth-child(2)').allInnerTexts()).map(
    (т) => т.match(/·\s*(\d+:\d+)\s*$/)?.[1],
  )
  expect(
    ключи.filter((к) => !к || !свои.has(поКлючу.get(к)!)),
    'на экране заявок чужих участков нет',
  ).toEqual([])
  const счёт = await page.getByText(/^\d+–\d+ из \d+$/).innerText()
  const заявокТехника = Number(счёт.match(/из (\d+)$/)![1])
  expect(заявокТехника, 'строк на экране столько же, сколько заявок').toBe(ключи.length)
  expect(заявокТехника, 'заявок у техника меньше, чем у диспетчера района').toBeLessThan(
    заявкиДиспетчера.total,
  )
})

test('US-21 сц. 2: Решения техник не принимает', async ({ page, browser }) => {
  // Прогноз своего участка — чужой техник и не откроет (US-16 сц. 2).
  const { items } = (await (await page.request.get('/api/forecasts?limit=1')).json()) as {
    items: { forecast_id: number }[]
  }
  const id = items[0].forecast_id

  await page.goto(`/forecasts/${id}`)
  // data-can-decide появляется, когда пришёл /api/auth/me: раньше «кнопки нет»
  // проверялось бы до того, как она могла появиться.
  await expect(page.getByTestId('last-decision')).toHaveAttribute('data-can-decide', 'false', {
    timeout: 30_000,
  })
  await expect(page.getByTestId('outcome')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Решение диспетчера' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Отметить исход' })).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Сервер держит то же правило: запись решения и исхода технику закрыта.
  for (const путь of ['feedback', 'outcome']) {
    const r = await page.request.post(`/api/forecasts/${id}/${путь}`, { data: {} })
    expect(r.status(), `POST /api/forecasts/${id}/${путь} от техника`).toBe(403)
  }

  // Контроль: та же карточка у диспетчера района — обе кнопки на месте,
  // значит у техника их нет из-за роли, а не из-за селектора.
  const контекст = await browser.newContext({
    extraHTTPHeaders: { 'X-User-Login': ДИСПЕТЧЕР_РАЙОНА },
  })
  const диспетчер = await контекст.newPage()
  await диспетчер.goto(`/forecasts/${id}`)
  await expect(диспетчер.getByTestId('last-decision')).toHaveAttribute('data-can-decide', 'true', {
    timeout: 30_000,
  })
  await expect(диспетчер.getByRole('button', { name: 'Решение диспетчера' })).toBeVisible()
  await expect(диспетчер.getByRole('button', { name: 'Отметить исход' })).toBeVisible()
  await контекст.close()
})
