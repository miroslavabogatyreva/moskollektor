// Узел дерева объектов слева от схемы отбирает датчики и в виде «по датчикам»:
// до 28.09.2026 выбор «Шкаф ОПС объект Вита» менял только ось «по участкам»,
// а прогноз по датчикам оставался на весь коллектор (Слава, скриншот 28.09.2026).
// Против живого стенда, только чтение; E2E_BUNDLE=dist — своя сборка.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })
test.beforeEach(({ page }) => свойБандл(page))

interface Узел {
  object_id: number
  name: string
  section_ids: number[]
}

test('узел дерева сужает прогноз по датчикам до своих участков', async ({ page }) => {
  const дерево = (await (await page.request.get('/api/objects/tree')).json()) as {
    object_id: number
    name: string
    nodes: Узел[]
  }[]
  // Коллектор и узел, у которого датчиков с прогнозом больше нуля, но меньше, чем у коллектора:
  // иначе отбор ничего не доказывает.
  let найдено: { к: number; у: Узел; всего: number; уУзла: number } | null = null
  for (const к of дерево) {
    if (к.nodes.length < 2) continue
    const r = await page.request.get(
      `/api/sensor-risk?synthetic=1&collector=${к.object_id}&limit=5000`,
    )
    const items = ((await r.json()) as { items: { section_id: number | null }[] }).items
    for (const у of к.nodes) {
      const свои = new Set(у.section_ids)
      const уУзла = items.filter((s) => s.section_id != null && свои.has(s.section_id)).length
      if (уУзла > 0 && уУзла < items.length) {
        найдено = { к: к.object_id, у, всего: items.length, уУзла }
        break
      }
    }
    if (найдено) break
  }
  expect(найдено, 'на стенде есть узел с частью датчиков коллектора').not.toBeNull()
  const { к, у, всего, уУзла } = найдено!

  await page.goto('/map')
  await page.locator('main select').first().selectOption(String(к))
  const демо = page.getByTestId('sensor-demo')
  await expect(демо).toContainText(`${всего} датчик`)

  await page
    .getByRole('navigation', { name: 'Дерево объектов' })
    .getByRole('button', { name: new RegExp(`^${у.name}`) })
    .click()
  await expect(демо.getByRole('heading', { level: 2 })).toContainText(у.name)
  await expect(демо).toContainText(`${уУзла} датчик`)

  // Повторный клик снимает отбор — снова весь коллектор.
  await page
    .getByRole('navigation', { name: 'Дерево объектов' })
    .getByRole('button', { name: new RegExp(`^${у.name}`) })
    .click()
  await expect(демо).toContainText(`${всего} датчик`)
})
