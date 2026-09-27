// US-12 «Журнал технологических событий» — docs/user-stories.md, MOS-54 (план 5.7),
// приёмка Ф-89. Таблица TechEventsTable в карточке участка /objects/:sectionId
// читает GET /api/tech-events (backend/app/api/tech_events.py) только по своему
// участку. Окно по умолчанию — последние сутки выгрузки, его выбирает сервер.
import { expect, test, type Page } from '@playwright/test'

interface TechEvent {
  journal_id: number
  read_time: string
  object: string | null
  event_type: string
}

// Участок с тревогами за окно по умолчанию: имя объекта в журнале — это
// «Коллектор <префикс smvu_key>, пикет <суффикс>», section_id берём из справочника.
async function участокСТревогами(page: Page): Promise<{ id: number; name: string }> {
  const r = await page.request.get('/api/tech-events?event_type=Предупреждение&limit=1')
  const [e] = ((await r.json()) as { items: TechEvent[] }).items
  const m = e.object!.match(/^Коллектор (\d+), пикет (\d+)$/)!
  const sections = (await (await page.request.get('/data/sections.json')).json()) as {
    section_id: number
    smvu_key: string
  }[]
  const s = sections.find((x) => x.smvu_key === `${m[1]}:${m[2]}`)!
  return { id: s.section_id, name: e.object! }
}

const журнал = (page: Page) => page.getByRole('region', { name: 'Журнал технологических событий' })
const таблица = (page: Page) => журнал(page).getByRole('table')
const КОЛОНКИ = ['Время регистрации', 'Объект', 'Тип датчика', 'Событие датчика', 'Тип события']

test('US-12 сц. 1: пять колонок формы заказчика', async ({ page }) => {
  const u = await участокСТревогами(page)
  await page.goto(`/objects/${u.id}`)
  const t = таблица(page)
  for (const h of КОЛОНКИ) await expect(t.getByRole('columnheader', { name: h })).toBeVisible()
  await expect(t.locator('tbody tr').first()).toBeVisible({ timeout: 15_000 })
  // В карточке — только свой участок: «Коллектор 797, пикет 1» не тянет пикеты 19 и 178.
  const объекты = await t.locator('tbody tr td:nth-child(2)').allTextContents()
  expect(new Set(объекты)).toEqual(new Set([u.name]))
})

test('US-12 сц. 2: фильтр по колонке', async ({ page }) => {
  const u = await участокСТревогами(page)
  await page.goto(`/objects/${u.id}`)
  const t = таблица(page)
  await expect(t.locator('tbody tr').first()).toBeVisible({ timeout: 15_000 })
  await журнал(page).getByLabel('Тип события').selectOption('Предупреждение')
  const ответ = page.waitForResponse((r) => r.url().includes('event_type=%D0%9F'))
  await журнал(page).getByRole('button', { name: 'Применить' }).click()
  await ответ
  const типы = await t.locator('tbody tr td:nth-child(5)').allTextContents()
  expect(типы.length).toBeGreaterThan(0)
  expect(new Set(типы)).toEqual(new Set(['Предупреждение']))
})

test('US-12 сц. 2: сортировка по колонке', async ({ page }) => {
  const u = await участокСТревогами(page)
  await page.goto(`/objects/${u.id}`)
  await expect(таблица(page).locator('tbody tr').first()).toBeVisible({ timeout: 15_000 })
  const ответ = page.waitForResponse(
    (r) =>
      r.url().includes('/api/tech-events') &&
      r.url().includes('sort=sensor_kind') &&
      r.url().includes('order=asc'),
  )
  await таблица(page).getByRole('button', { name: 'Тип датчика' }).click()
  await ответ
  await expect(таблица(page).getByRole('columnheader', { name: 'Тип датчика' })).toHaveAttribute(
    'aria-sort',
    'ascending',
  )
})

test('US-12 сц. 3: диапазон дат', async ({ page }) => {
  const u = await участокСТревогами(page)
  await page.goto(`/objects/${u.id}`)
  await expect(таблица(page).locator('tbody tr').first()).toBeVisible({ timeout: 15_000 })
  await журнал(page).getByLabel('С даты').fill('2026-06-29')
  await журнал(page).getByLabel('По дату').fill('2026-06-29')
  const ответ = page.waitForResponse(
    (r) =>
      r.url().includes('/api/tech-events') &&
      r.url().includes('from=2026-06-29') &&
      r.url().includes('to=2026-06-29'),
  )
  await журнал(page).getByRole('button', { name: 'Применить' }).click()
  const тело = (await (await ответ).json()) as { items: TechEvent[] }
  // Сервер режет по суткам Москвы — сверяем даты строк в том же поясе.
  for (const e of тело.items) {
    expect(new Date(e.read_time).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })).toBe(
      '29.06.2026',
    )
  }
})

test('US-12 сц. 4: событие СМВУ появляется само', async ({ page }) => {
  await page.clock.install()
  const u = await участокСТревогами(page)
  await page.goto(`/objects/${u.id}`)
  await expect(таблица(page).locator('tbody tr').first()).toBeVisible({ timeout: 15_000 })
  await expect(журнал(page).getByLabel('Автообновление')).toBeChecked()

  // Следующий опрос отдаёт подставное событие поверх настоящего ответа.
  await page.route('**/api/tech-events?**', async (route) => {
    const r = await route.fetch()
    const body = (await r.json()) as { total: number; items: object[] }
    body.items.unshift({
      journal_id: 1,
      read_time: new Date().toISOString(),
      object: u.name,
      sensor_kind: 'Датчик дыма',
      value_text: 'Проверка автообновления',
      event_type: 'Предупреждение',
    })
    await route.fulfill({ response: r, json: { ...body, total: body.total + 1 } })
  })
  await page.clock.runFor(60_000)
  await expect(таблица(page)).toContainText('Проверка автообновления')

  // Выключили автообновление — следующая минута запроса не шлёт.
  await журнал(page).getByLabel('Автообновление').uncheck()
  let запросов = 0
  page.on('request', (r) => r.url().includes('/api/tech-events') && запросов++)
  await page.clock.runFor(60_000)
  await page.waitForTimeout(300)
  expect(запросов).toBe(0)
})

// Имя «Коллектор 797, пикет 1» — подстрока ещё 249 имён из 3 173: фильтр object
// (ILIKE) за июнь 2026 отдаёт по нему 84 события пикетов 100 и 137, а своих у
// пикета 1 ноль. Карточка отбирает по section_id и чужих не показывает.
test('US-12 сц. 1: в карточке нет событий соседнего пикета с похожим именем', async ({ page }) => {
  const подстрока = await page.request.get(
    '/api/tech-events?from=2026-06-01&to=2026-06-30&limit=1000&object=' +
      encodeURIComponent('Коллектор 797, пикет 1'),
  )
  const чужие = ((await подстрока.json()) as { items: TechEvent[] }).items.filter(
    (e) => e.object !== 'Коллектор 797, пикет 1',
  )
  expect(
    чужие.length,
    'положительный контроль: подстрока правда тянет чужие пикеты',
  ).toBeGreaterThan(0)

  await page.goto('/objects/1423') // smvu_key 797:1
  await журнал(page).getByLabel('С даты').fill('2026-06-01')
  await журнал(page).getByLabel('По дату').fill('2026-06-30')
  const ответ = page.waitForResponse(
    (r) => r.url().includes('/api/tech-events') && r.url().includes('from=2026-06-01'),
  )
  await журнал(page).getByRole('button', { name: 'Применить' }).click()
  expect((await ответ).url()).toContain('section_id=1423')
  await expect(page.getByText('Событий за период нет')).toBeVisible()
  await expect(таблица(page)).not.toContainText('пикет 100')
  await expect(таблица(page)).not.toContainText('пикет 137')
})
