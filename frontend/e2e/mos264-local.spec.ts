// Run via docs/proof/2026-09-28-mos264-review/local_e2e.py.
// Actual model, worker, database and API; no route interception or mocks.
import { expect, test } from '@playwright/test'
import { процент, type SensorRiskPage } from '../src/lib/sensorRisk'

test('MOS-264: calculated synthetic and real modes survive UI toggle and reload', async ({ page, request }) => {
  test.skip(process.env.MOS264_LOCAL_E2E !== '1', 'requires disposable local database harness')
  expect(new URL(process.env.BASE_URL!).hostname).toBe('127.0.0.1')
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  const modes: SensorRiskPage[] = []
  const counts: number[] = []
  for (const mode of [0, 1]) {
    const response = await request.get(`/api/sensor-risk?synthetic=${mode}&limit=50`)
    expect(response.ok()).toBeTruthy()
    modes[mode] = await response.json()
    const summary = await request.get(`/api/sensor-risk/summary?synthetic=${mode}`)
    expect(summary.ok()).toBeTruthy()
    counts[mode] = (await summary.json()).high
  }
  expect(modes[1].items.some(s => s.reasons.some(r => r.kind === 'synthetic'))).toBeTruthy()
  expect(modes[0].items.every(s => s.equipment === null)).toBeTruthy()
  await page.goto('/dashboard')
  const toggle = page.getByTestId('synthetic-toggle')
  const rows = page.getByTestId('sensor-table').locator('tbody tr')
  const verify = async (mode: number) => {
    await expect(rows).toHaveCount(50)
    await expect(rows.first()).toHaveAttribute('data-channel-id', String(modes[mode].items[0].channel_id))
    await expect(rows.first().locator('td').nth(1)).toHaveText(процент(modes[mode].items[0].score))
    await expect(page.locator('article', { hasText: 'Высокий риск' }).locator('div').first()).toHaveText(String(counts[mode]))
  }
  await expect(toggle).toBeChecked()
  await expect(page.getByTestId('synthetic-note')).toBeVisible()
  await verify(1)
  await toggle.uncheck()
  await expect(page).toHaveURL(/synthetic=0/)
  await expect(page.getByTestId('synthetic-note')).toHaveCount(0)
  await verify(0)
  await page.reload()
  await expect(toggle).not.toBeChecked()
  await verify(0)
  await toggle.check()
  await verify(1)
  await expect(page.getByTestId('synthetic-note')).toBeVisible()
  expect(errors).toEqual([])
})
