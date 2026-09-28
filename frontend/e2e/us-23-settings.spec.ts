// US-23 «Настроить пороги без выкладки» — docs/user-stories.md, MOS-208, приёмка НФ-43,
// НФ-44. Экран /admin/settings читает GET /api/settings и правит одну настройку
// PUT /api/settings/{key} (backend/app/api/settings.py); старое и новое значение
// промежуточный слой кладёт в audit.user_action.details, экран журнала их показывает.
//
// ВНИМАНИЕ: сц. 2 и 5 ПИШУТ НА СТЕНД. Каждый поднимает порог автозаявки класса A
// (order_threshold_a) на 0,01 и в finally возвращает прежнее значение — на стенде
// остаются две строки журнала действий на прогон и changed_by/changed_at у настройки.
// Поднимаем, а не опускаем: между правкой и возвратом проходит несколько секунд,
// и попади туда расчёт, более строгий порог не заведёт ни одной лишней заявки.
// Порог высокого риска тест не трогает вовсе: он красит дашборд всем.
import { expect, test, type APIRequestContext } from '@playwright/test'

const ADMIN = { 'X-User-Login': 'admin1' }
const ODS = { 'X-User-Login': 'ods1' }
const КЛЮЧ = 'order_threshold_a'

interface Setting {
  key: string
  value: number
}

async function настройка(request: APIRequestContext, key: string): Promise<number> {
  const r = await request.get('/api/settings', { headers: ADMIN })
  expect(r.status()).toBe(200)
  const s = ((await r.json()) as Setting[]).find((x) => x.key === key)
  expect(s, `в GET /api/settings есть ${key}`).toBeTruthy()
  return s!.value
}

async function вернуть(request: APIRequestContext, key: string, value: number) {
  const r = await request.put(`/api/settings/${key}`, {
    headers: ADMIN,
    data: { value: String(value) },
  })
  expect(r.status(), `${key} возвращён к ${value}`).toBe(200)
}

// Значение для <input type="datetime-local"> в поясе браузера, с точностью до минуты.
function местноеВремя(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

const выше = (x: number, на: number) => String(Math.round((x + на) * 100) / 100)

// С 28.09.2026 класс high даёт уровень датчика (правила, run_sensors.py), а не порог
// risk_threshold_high: на экране «Настройки» порог только для чтения. Сценарий
// проверяет, что дашборд показывает «высокий риск» ровно у тех участков, которым
// класс high дал расчёт.
test('US-23 сц. 1: «высокий риск» на дашборде — у участков с классом high расчёта', async ({
  browser,
}) => {
  const ctx = await browser.newContext({ extraHTTPHeaders: ODS })
  const page = await ctx.newPage()
  const риски = page.waitForResponse((r) => r.url().endsWith('/api/risks'))
  await page.goto('/dashboard?view=sections&level=all')
  const строки = (await (await риски).json()) as { probability: number; risk_class: string }[]
  const неНижеПорога = строки.filter((r) => r.risk_class === 'high').length
  const таблица = page.locator('main table tbody tr')
  await expect(таблица).toHaveCount(строки.length, { timeout: 30_000 })
  await expect(таблица.filter({ hasText: 'высокий риск' })).toHaveCount(неНижеПорога)
  await ctx.close()
})

test.describe('под администратором', () => {
  test.use({ extraHTTPHeaders: ADMIN })

  test('US-23 сц. 2: сохраняется любая настройка', async ({ page, request }) => {
    const было = await настройка(request, КЛЮЧ)
    const стало = выше(было, 0.01)
    try {
      await page.goto('/dashboard')
      const пункт = page
        .getByRole('navigation', { name: 'Разделы' })
        .getByRole('link', { name: 'Настройки' })
      await expect(пункт).toBeVisible()
      await пункт.click()
      await expect(page).toHaveURL(/\/admin\/settings$/)
      const строка = page.getByRole('row', { name: /Порог автозаявки, класс A/ })
      await строка.getByRole('textbox').fill(стало)
      const [ответ] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().endsWith(`/api/settings/${КЛЮЧ}`) && r.request().method() === 'PUT',
        ),
        строка.getByRole('button', { name: 'Сохранить' }).click(),
      ])
      expect(ответ.status()).toBe(200)
      expect(await настройка(request, КЛЮЧ)).toBe(Number(стало))
      await expect(строка.getByText('сохранено')).toBeVisible()
    } finally {
      await вернуть(request, КЛЮЧ, было)
    }
  })

  test('US-23 сц. 5: правка настроек в журнале', async ({ page, request }) => {
    const было = await настройка(request, КЛЮЧ)
    // +0,02, а не +0,01, как в сц. 2: оба теста правят ключ в одну минуту,
    // и запись сц. 2 не должна засчитаться за эту.
    const стало = выше(было, 0.02)
    const момент = new Date()
    try {
      const r = await request.put(`/api/settings/${КЛЮЧ}`, {
        headers: ADMIN,
        data: { value: стало },
      })
      expect(r.status()).toBe(200)

      await page.goto('/dashboard')
      await page
        .getByRole('navigation', { name: 'Разделы' })
        .getByRole('link', { name: 'Журнал действий' })
        .click()
      await expect(page).toHaveURL(/\/admin\/audit$/)
      await page.getByLabel('Логин').fill('admin1')
      await page.getByLabel('С момента').fill(местноеВремя(момент))
      await page.getByRole('button', { name: 'Найти' }).click()
      const запись = page
        .locator('tbody tr')
        .filter({ hasText: `/api/settings/${КЛЮЧ}` })
        .filter({ hasText: `${было} → ${стало}` })
      // Кто, когда, какой ключ, старое и новое значение — в одной строке журнала.
      await expect(запись.first()).toBeVisible()
      await expect(запись.first()).toContainText('admin1')
      await expect(запись.first().locator('td').first()).toHaveText(
        /\d{2}\.\d{2}\.\d{4},? \d{2}:\d{2}/,
      )
    } finally {
      await вернуть(request, КЛЮЧ, было)
    }
  })
})

test.describe('под диспетчером ОДС', () => {
  test.use({ extraHTTPHeaders: ODS })

  test('US-23 сц. 3: пункта настроек у других ролей нет', async ({ page }) => {
    // Пункты администратора Nav дорисовывает по ответу /api/auth/me: без ожидания
    // проверка «пункта нет» прошла бы и до ответа, на любой роли.
    const кто = page.waitForResponse((r) => r.url().endsWith('/api/auth/me'))
    await page.goto('/dashboard')
    expect(((await (await кто).json()) as { roles: string[] }).roles).toEqual(['ods_dispatcher'])
    const меню = page.getByRole('navigation', { name: 'Разделы' })
    await expect(меню.getByRole('link', { name: 'Дашборд рисков' })).toBeVisible()
    await expect(меню.getByRole('link', { name: 'Настройки' })).toHaveCount(0)
    await page.goto('/admin/settings')
    await expect(page.getByText('Настройки доступны только администратору')).toBeVisible()
  })

  test('US-23 сц. 4: API настроек другим ролям закрыт', async ({ request }) => {
    // Шлём текущее значение: пропусти сервер запрос, настройка всё равно не изменится.
    const было = await настройка(request, КЛЮЧ)
    const r = await request.put(`/api/settings/${КЛЮЧ}`, {
      headers: ODS,
      data: { value: String(было) },
    })
    expect(r.status()).toBe(403)
  })
})
