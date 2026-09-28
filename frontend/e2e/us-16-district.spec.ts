// US-16. Видеть только свой район — docs/user-stories.md, приёмка Ф-94, НФ-44.
//
// Район в выгрузке заказчика один (узел 5773 «Район по эксплуатации»), поэтому
// «район А» подопытного disp2 — два коллектора, 5 «объект Альфа» и 7 «объект
// Гамма» (db/seed/rbac.sql). disptech — диспетчер того же «района А» и техник
// комплекса 4068 «объект Сигма» из «района Б».
import { expect, test } from '@playwright/test'
import { проверитьЭкраны, видимые } from './helpers/scope'

// E2E_US16_LOGIN — прогнать сц. 1–3 под другой ролью с областью видимости (tech1)
// до того, как сид с disp2 доехал до стенда.
const РАЙОН_А = process.env.E2E_US16_LOGIN ?? 'disp2'
// Участок чужого района — любой из справочника, которого нет в /api/risks роли.
async function чужой(page: import('@playwright/test').Page): Promise<number> {
  const свои = await видимые(page)
  const участки = (await (await page.request.get('/data/sections.json')).json()) as {
    section_id: number
  }[]
  return участки.find((у) => !свои.has(у.section_id))!.section_id
}

test.describe('диспетчер района А', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': РАЙОН_А } })

  test('US-16 сц. 1: списки — только свой район', async ({ page }) => {
    test.setTimeout(120_000)
    const свои = await видимые(page)
    expect(свои.size, 'у района А есть участки').toBeGreaterThan(0)
    const статус = (await (await page.request.get('/api/data-status')).json()) as {
      sections_total: number
    }
    expect(свои.size, 'участков района А в базе столько же, сколько в /api/risks').toBe(
      статус.sections_total,
    )
    const всего = (await (
      await page.request.get('/api/risks', { headers: { 'X-User-Login': 'ods1' } })
    ).json()) as unknown[]
    expect(свои.size, 'район А — не весь парк').toBeLessThan(всего.length)
    await проверитьЭкраны(page, свои)
  })

  test('US-16 сц. 2: чужой участок через API закрыт', async ({ page }) => {
    const id = await чужой(page)
    const r = await page.request.get(`/api/objects/${id}`)
    expect(r.status()).toBe(403)
  })

  test('US-16 сц. 3: чужой участок по ссылке назван чужим', async ({ page }) => {
    const id = await чужой(page)
    await page.goto(`/objects/${id}`)
    await expect(page.getByTestId('out-of-scope')).toContainText(
      `Участок ${id} вне вашего района или комплекса`,
    )
  })
})

test.describe('диспетчер района А и техник комплекса Сигма', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': 'disptech' } })

  test('US-16 сц. 4: две роли — объединение', async ({ page }) => {
    const обе = await видимые(page)
    const r = await page.request.get('/api/risks', { headers: { 'X-User-Login': РАЙОН_А } })
    const районА = new Set(((await r.json()) as { section_id: number }[]).map((x) => x.section_id))
    const участки = (await (await page.request.get('/data/sections.json')).json()) as {
      section_id: number
      collector: number
    }[]
    const сигма = участки.filter((у) => у.collector === 4068).map((у) => у.section_id)
    expect(сигма.length).toBeGreaterThan(0)
    expect(обе.size, 'объединение: район А плюс комплекс Сигма').toBe(районА.size + сигма.length)
    for (const id of [...районА, ...сигма]) expect(обе.has(id)).toBe(true)

    await page.goto('/dashboard?view=sections')
    await expect(page.locator('main table tbody tr')).toHaveCount(обе.size, { timeout: 30_000 })
  })
})
