import { test, expect } from '@playwright/test'

test('US-01 сц. 5: первые десять участков имеют локальный прогноз на 24 часа', async ({
  page,
  request,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await page.goto('/dashboard')
  await expect(page.getByText('Архивная проверка: локальный прогноз на 24 часа')).toBeVisible()
  await expect(
    page.getByText('Первые 10 участков для проверки; это ранг, а не высокий класс риска.'),
  ).toBeVisible()
  const response = await request.get('/api/risks')
  expect(response.ok()).toBeTruthy()
  const risks = await response.json()
  expect(risks.length).toBeGreaterThan(10)
  const expected = [...risks].sort((a, b) => a.risk_rank - b.risk_rank).slice(0, 10)
  const picked = page.locator('tr[data-local24-selected="true"]')
  await expect(picked).toHaveCount(10)
  for (let i = 0; i < 10; i++) {
    await expect(picked.nth(i)).toHaveAttribute('data-section-id', String(expected[i].section_id))
    await expect(picked.nth(i).locator('[data-local24-probability]')).toHaveText(
      expected[i].probability.toFixed(6),
    )
    await expect(picked.nth(i).locator('[data-risk-class]')).toHaveAttribute(
      'data-risk-class',
      expected[i].risk_class ?? '',
    )
  }
  expect(new Set(expected.map((row) => row.probability)).size).toBeGreaterThan(1)
  expect(errors).toEqual([])
})
