// Демо «прогноз на уровне датчика» на узле «объект Каппа ДУ» (5657): экран
// /map?demo=sensors, компонент src/screens/map/SensorDemo.tsx, метод
// GET /api/sensor-risk?node=5657.
//
// По умолчанию идёт против стенда, как соседние тесты. Два переключателя:
// E2E_SENSOR_MOCK=1 — ответ /api/sensor-risk берётся из fixtures/sensor-risk-5657.json
//   (балл и причины — docs/proof/2026-09-28-sensor-level/scores.json, паспорта выдуманы);
//   нужен, пока метода на стенде нет.
// E2E_BUNDLE=dist — вместо index.html и /assets/ стенда отдать локальную сборку
//   (npm run build): новый экран проверяется против живого API стенда до выкладки.
// Пути относительные — запускать из frontend/.
import { expect, test } from '@playwright/test'

const МОК = process.env.E2E_SENSOR_MOCK === '1'
const БАНДЛ = process.env.E2E_BUNDLE

test.beforeEach(async ({ page }) => {
  if (МОК)
    await page.route('**/api/sensor-risk?**', (r) =>
      r.fulfill({ path: 'e2e/fixtures/sensor-risk-5657.json' }),
    )
  if (БАНДЛ)
    await page.route(
      (url) => !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/data/'),
      (r) => {
        const путь = new URL(r.request().url()).pathname
        r.fulfill({
          path: путь.startsWith('/assets/') ? БАНДЛ + путь : `${БАНДЛ}/index.html`,
        })
      },
    )
})

test('демо по датчикам: на ПК632 видны и плохие, и нормальные датчики, газовый снят по ППР', async ({
  page,
}) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))

  await page.goto('/map?demo=sensors')
  const демо = page.getByTestId('sensor-demo')
  await expect(демо).toBeVisible({ timeout: 30_000 })
  await expect(демо.getByRole('note')).toContainText('паспорта оборудования синтетические')

  // По умолчанию выбран пикет датчика с самым высоким баллом — ПК632.
  await expect(демо.locator('g[data-picket="632"][data-selected]')).toHaveCount(1)
  await expect(демо.getByTestId('sensor-picket')).toContainText('ПК632')

  const строки = демо.locator('li[data-channel-id]')
  expect(await строки.count()).toBeGreaterThanOrEqual(30)
  expect(await строки.locator('[data-level="high"]').count()).toBeGreaterThanOrEqual(1)
  expect(await строки.locator('[data-level="normal"]').count()).toBeGreaterThanOrEqual(1)

  // Газовый датчик на ПК632: отказ 04.06.2026 — плановый демонтаж по графику ППР.
  const газ = строки.filter({ hasText: 'ГАЗ' }).first()
  await expect(газ.locator('[data-kind="plan"]')).toContainText('ППР')

  // Первый датчик раскрыт: причины с весами и ссылка на карточку участка.
  const первый = строки.first()
  await expect(первый.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expect(первый.locator('[data-kind="real"]').first()).toBeVisible()
  await expect(первый.getByRole('link')).toHaveAttribute('href', /^\/objects\/\d+$/)

  // Клик по другой стопке меняет список.
  await демо.locator('g[data-picket="730"]').click()
  await expect(демо.getByTestId('sensor-picket')).toContainText('ПК730')

  expect(ошибки).toEqual([])
})
