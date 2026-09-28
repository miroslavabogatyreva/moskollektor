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

// Пикеты тест берёт из ответа /api/sensor-risk (фикстура или стенд), а не зашивает:
// с SL.10 (MOS-263) уровни считает модель, и самый рискованный пикет меняется.
interface Строка {
  channel_id: number
  picket: number | null
  level: 'high' | 'watch' | 'normal'
  reasons: { kind: string }[]
}

test('демо по датчикам: на пикете видны датчики с риском и в норме, газовый снят по ППР', async ({
  page,
}) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))

  const ответ = page.waitForResponse(
    (r) => r.url().includes('/api/sensor-risk?') && r.request().method() === 'GET',
  )
  await page.goto('/map?demo=sensors')
  const наОси = ((await (await ответ).json()).items as Строка[]).filter((s) => s.picket != null)
  const демо = page.getByTestId('sensor-demo')
  await expect(демо).toBeVisible({ timeout: 30_000 })
  // Плашка переключателя синтетики (SL.5, SL.6): «Демо: паспорта синтетические».
  await expect(демо.getByRole('note')).toContainText('Демо: паспорта синтетические')

  // По умолчанию выбран пикет датчика с самым высоким баллом — первого в ответе.
  const пк = наОси[0].picket!
  await expect(демо.locator(`g[data-picket="${пк}"][data-selected]`)).toHaveCount(1)
  await expect(демо.getByTestId('sensor-picket')).toContainText(`ПК${пк}`)

  // Сначала список — только датчики с риском и снятые по ППР; «в норме» свёрнуты кнопкой.
  const наПикете = наОси.filter((s) => s.picket === пк)
  const строки = демо.locator('li[data-channel-id]')
  await expect(строки.locator('button [data-level="high"]')).toHaveCount(
    наПикете.filter((s) => s.level === 'high').length,
  )
  const свёрнуто = демо.getByRole('button', { name: /^Показать ещё \d+ в норме$/ })
  if (await свёрнуто.count()) await свёрнуто.click()
  await expect(строки).toHaveCount(наПикете.length)

  // Первый датчик раскрыт: причины и ссылка на карточку участка.
  const первый = строки.first()
  await expect(первый.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expect(первый.getByRole('link')).toHaveAttribute('href', /^\/objects\/\d+$/)

  // Газовый датчик, снятый по графику ППР: причина «плановый демонтаж» на его пикете.
  const газ = наОси.find((s) => s.reasons.some((r) => r.kind === 'plan'))
  if (газ) {
    await демо.locator(`g[data-picket="${газ.picket}"]`).click()
    await expect(демо.getByTestId('sensor-picket')).toContainText(`ПК${газ.picket}`)
    await expect(
      демо.locator(`li[data-channel-id="${газ.channel_id}"] [data-kind="plan"]`),
    ).toContainText('ППР')
  }

  // Клик по другой стопке меняет список.
  const другой = наОси.find((s) => s.picket !== пк)!.picket!
  await демо.locator(`g[data-picket="${другой}"]`).click()
  await expect(демо.getByTestId('sensor-picket')).toContainText(`ПК${другой}`)

  expect(ошибки).toEqual([])
})
