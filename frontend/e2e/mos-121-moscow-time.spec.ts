// MOS-121 (план 5.11), строки приёмки М-12 и Ф-33: все даты на экране — по Москве,
// в каком бы поясе ни стоял браузер. Браузер ставим во Владивосток (UTC+10): там
// часы расходятся с московскими на 7, и любая дата, собранная в поясе браузера,
// видна сразу. Числа берём у стенда тем же запросом, что и экран, а ожидаемую
// строку считаем здесь, своим Intl с Europe/Moscow — не функцией из src/lib/format.ts,
// иначе тест проверял бы код самим собой.
import { expect, test, type Page } from '@playwright/test'
import { account, demoAccounts, loginAs } from './helpers/auth'

test.use({ timezoneId: 'Asia/Vladivostok' })

// "29.06.2026 23:59" или с секундами "29.06.2026 23:59:59" — московское время момента.
function мск(iso: string, секунды = false): string {
  return new Date(iso)
    .toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      ...(секунды ? { second: '2-digit' } : {}),
      hourCycle: 'h23',
    })
    .replace(',', '')
}

const мскДата = (iso: string) => мск(iso).slice(0, 10)

// "2026-06-29T23:59" — московская минута момента для <input type="datetime-local">.
function мскМинута(iso: string): string {
  const [д, в] = мск(iso).split(' ')
  const [день, месяц, год] = д.split('.')
  return `${год}-${месяц}-${день}T${в}`
}

async function jsonОтвета<T>(
  page: Page,
  часть: string,
  действие: () => Promise<unknown>,
): Promise<T> {
  const [ответ] = await Promise.all([
    page.waitForResponse((r) => r.url().includes(часть) && r.ok()),
    действие(),
  ])
  return (await ответ.json()) as T
}

test('браузер действительно во Владивостоке, иначе тест ничего не доказывает', async ({ page }) => {
  await page.goto('/login')
  expect(await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)).toBe(
    'Asia/Vladivostok',
  )
})

test('дашборд: время расчёта и дата данных по Москве', async ({ page }) => {
  const статус = await jsonОтвета<{ data_edge: string; computed_at: string }>(
    page,
    '/api/data-status',
    () => page.goto('/dashboard'),
  )
  await expect(page.getByText(`расчёт от ${мск(статус.computed_at, true)}`)).toBeVisible()
  await expect(page.getByText(мскДата(статус.data_edge), { exact: true })).toBeVisible()
})

test('шапка: «обновлено в» — московские часы', async ({ page }) => {
  await page.goto('/dashboard')
  const шапка = page.getByRole('banner')
  await expect(шапка).toContainText(/обновлено в \d\d:\d\d:\d\d/)
  const [ч, м, с] = ((await шапка.textContent())?.match(/обновлено в (\d\d):(\d\d):(\d\d)/) ?? [])
    .slice(1)
    .map(Number)
  const сейчас = мск(new Date().toISOString(), true).slice(11).split(':').map(Number)
  const секунд = (x: number[]) => x[0] * 3600 + x[1] * 60 + x[2]
  const разница = Math.abs(секунд([ч, м, с]) - секунд(сейчас))
  // Опрос раз в минуту, плюс запас; во Владивостоке разница была бы 7 часов.
  expect(Math.min(разница, 86400 - разница), 'расхождение с Москвой, секунд').toBeLessThan(120)
})

test('журнал прогнозов: московские сутки в фильтре и время расчёта по Москве', async ({ page }) => {
  const список = await jsonОтвета<{ items: { computed_at: string }[] }>(
    page,
    '/api/forecasts',
    () => page.goto('/log'),
  )
  const сегодня = мскМинута(new Date().toISOString()).slice(0, 10)
  await expect(page.getByLabel('С даты')).toHaveValue(сегодня)
  await expect(page.getByLabel('По дату')).toHaveValue(сегодня)
  expect(
    список.items.length,
    'за сегодняшние сутки прогнозов нет — сверять нечего',
  ).toBeGreaterThan(0)
  await expect(
    page.locator('tbody').getByText(мск(список.items[0].computed_at, true)).first(),
  ).toBeVisible()
})

test('карточка объекта: последняя запись, прогнозы и лента показаний по Москве', async ({
  page,
}) => {
  const риски = await (await page.request.get('/api/risks')).json()
  const участок = (риски as { section_id: number; risk_rank: number }[]).sort(
    (a, b) => a.risk_rank - b.risk_rank,
  )[0].section_id

  const [объект, показания] = await Promise.all([
    page
      .waitForResponse((r) => new URL(r.url()).pathname === `/api/objects/${участок}` && r.ok())
      .then((r) => r.json()),
    page
      .waitForResponse((r) => r.url().includes(`/api/objects/${участок}/readings`) && r.ok())
      .then((r) => r.json()),
    page.goto(`/objects/${участок}`),
  ])
  const о = объект as {
    last_reading_at: string | null
    recent_forecasts: { computed_at: string }[]
  }
  expect(о.last_reading_at, 'у участка с первым рангом нет ни одной записи').not.toBeNull()
  await expect(
    page.getByText(`Последняя запись участка: ${мск(о.last_reading_at!, true)}`),
  ).toBeVisible()
  if (о.recent_forecasts.length > 0) {
    await expect(page.getByText(мск(о.recent_forecasts[0].computed_at, true)).first()).toBeVisible()
  }

  // Подсказки на ленте состояний: каждая несёт время показания, и оно московское.
  const ожидаемые = new Set(
    (показания as { read_time: string }[]).map((r) => мск(r.read_time, true)),
  )
  // Ленту рисуют только каналы состояний; у участка из одних числовых каналов
  // подсказок нет, и цикл ниже пройдёт впустую — поэтому число сверенных
  // подсказок пишем в отчёт, а не прячем.
  const подсказки = await page.locator('svg rect title').allTextContents()
  test
    .info()
    .annotations.push({ type: 'подсказок ленты сверено', description: String(подсказки.length) })
  for (const т of подсказки) {
    const время = т.split(' · ').at(-1)!
    expect(ожидаемые.has(время), `подсказка «${т}» — не московское время показания`).toBe(true)
  }
})

test.describe('журнал действий пользователей', () => {
  test.use({ extraHTTPHeaders: {} })

  test('время строки и отбор по минуте — по Москве', async ({ page }) => {
    const admin = account(await demoAccounts(page), 'admin')
    await loginAs(page, admin.login, admin.password)
    const журнал = await jsonОтвета<{ items: { occurred_at: string }[] }>(page, '/api/audit', () =>
      page.goto('/admin/audit'),
    )
    expect(журнал.items.length, 'журнал действий пуст').toBeGreaterThan(0)
    const момент = журнал.items[0].occurred_at
    await expect(page.locator('tbody').getByText(мск(момент, true)).first()).toBeVisible()

    // Отбор «с минуты по ту же минуту» по Москве обязан найти эту же запись.
    const минута = мскМинута(момент)
    await page.getByLabel('С момента').fill(минута)
    await page.getByLabel('По момент').fill(минута)
    const [запрос] = await Promise.all([
      page.waitForRequest((r) => r.url().includes('/api/audit') && r.url().includes('from=')),
      page.getByRole('button', { name: 'Найти' }).click(),
    ])
    const from = new URL(запрос.url()).searchParams.get('from')!
    expect(new Date(from).toISOString()).toBe(new Date(`${минута}:00+03:00`).toISOString())
    await expect(page.locator('tbody').getByText(мск(момент, true)).first()).toBeVisible()
  })
})
