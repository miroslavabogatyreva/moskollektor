// Real API and customer-registry regression. No fabricated responses.
import { expect, test } from '@playwright/test'

interface Section {
  section_id: number
  smvu_key: string
  collector: number | null
  collector_ids: number[]
  mapping_status: string
}

test('collector mapping keeps ambiguous sections visible but off collector axes', async ({
  page,
  request,
}) => {
  const response = await request.get('/api/objects')
  expect(response.ok()).toBeTruthy()
  const sections = (await response.json()) as Section[]
  const ambiguous = sections.find((s) => s.smvu_key === '798:0')!
  expect(ambiguous.collector).toBeNull()
  expect(ambiguous.collector_ids).toEqual([6, 12])
  expect(ambiguous.mapping_status).toBe('ambiguous')
  const collectorIds = [
    ...new Set(sections.filter((s) => s.mapping_status === 'resolved').map((s) => s.collector)),
  ]
  expect(collectorIds).toHaveLength(16)

  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await page.goto('/map')
  const selector = page.locator('select').first()
  await expect(selector.locator('option')).toHaveCount(collectorIds.length)
  await selector.selectOption('12')
  const expected = sections.filter((s) => s.collector === 12 && s.mapping_status === 'resolved')
  const prefixes = new Set(expected.map((s) => s.smvu_key.split(':')[0]))
  await expect(page.locator('svg[role="img"]')).toHaveCount(prefixes.size)
  await expect(page.locator('svg[role="img"] title').filter({ hasText: '798:0 ·' })).toHaveCount(0)
  const titles = await page.locator('svg[role="img"] title').allTextContents()
  expect(titles.length).toBe(expected.length)
  expect(new Set(titles.map((title) => title.split(' · ')[0]))).toEqual(
    new Set(expected.map((s) => s.smvu_key)),
  )
  await expect(page.getByText(/локальный риск участка не оценён/)).toBeVisible()
  await page.getByRole('link', { name: 'Участок 798:0', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/objects/${ambiguous.section_id}$`))
  await expect(
    page.getByText(/Привязка к одному коллектору не подтверждена\. Коллекторы: 6, 12/),
  ).toBeVisible()
  expect(errors).toEqual([])
})
