// MOS-169, план 5.26, приёмка Ф-30: шкала метана в карточке объекта.
// «Подать значение метана 0,8 % об. — метка значения стоит между первой и второй
// ступенью, обе ступени видны на шкале». Карточка — из мока helpers/object-card-mock.ts.
//
// Без стенда: E2E_OFFLINE=1 BASE_URL=http://localhost:4173 (npm run build && npx vite preview).
import { expect, test, type Locator } from '@playwright/test'
import { mockObjectCard } from './helpers/object-card-mock'

const УЧАСТОК = 999001
const ГАЗ = { system_kind: 'Газовая охрана', sensor_kind: 'Газовый датчик' }
const ДЫМ = { system_kind: 'Пожарная охрана', sensor_kind: 'Датчик дыма', value_num: null }

const середина = async (l: Locator) => {
  const b = (await l.boundingBox())!
  return b.x + b.width / 2
}

test('Ф-30: метан 0,8 % об. — метка между двумя подписанными уставками', async ({ page }) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))
  await mockObjectCard(page, УЧАСТОК, [
    { channel_id: 1, name: 'Д1-Д21 ПК2-22', ...ГАЗ, value_num: 0.8 },
    { channel_id: 2, name: 'Д22-32 ПК2-61', ...ГАЗ, value_num: 1.7 },
    { channel_id: 3, name: 'ДД ПК40', ...ДЫМ },
  ])
  await page.goto(`/objects/${УЧАСТОК}`)

  const секция = page.getByTestId('gas-scales')
  await expect(секция).toBeVisible()
  await expect(секция.locator('[data-gas-scale]'), 'шкала только на газовые каналы').toHaveCount(2)

  const шкала = секция.locator('[data-gas-scale="1"]')
  await expect(шкала).toHaveAttribute('data-zone', 'between')
  await expect(шкала).toContainText('0,80 % об.')
  const первая = шкала.locator('[data-setpoint="0.75"]')
  const вторая = шкала.locator('[data-setpoint="1.5"]')
  await expect(первая).toBeVisible()
  await expect(вторая).toBeVisible()
  await expect(первая).toHaveText('0,75 % об.')
  await expect(вторая).toHaveText('1,5 % об.')

  const метка = шкала.locator('[data-value-marker]')
  await expect(метка).toBeVisible()
  // Отметка уставки — черта шириной 2 px по левому краю блока: её x и есть уставка.
  const x = await середина(метка)
  expect(x, 'метка правее первой ступени').toBeGreaterThan(await середина(первая))
  expect(x, 'метка левее второй ступени').toBeLessThan(await середина(вторая))

  await expect(секция.locator('[data-gas-scale="2"]')).toHaveAttribute('data-zone', 'above')
  await expect(page.getByRole('meter', { name: 'Метан, Д1-Д21 ПК2-22' })).toHaveAttribute(
    'aria-valuenow',
    '0.8',
  )
  expect(ошибки).toEqual([])
})

test('Ф-30: на участке без газовых каналов секции нет', async ({ page }) => {
  await mockObjectCard(page, УЧАСТОК, [{ channel_id: 3, name: 'ДД ПК40', ...ДЫМ }])
  await page.goto(`/objects/${УЧАСТОК}`)
  await expect(page.getByText('Отказы по каналам')).toBeVisible()
  await expect(page.getByTestId('gas-scales')).toHaveCount(0)
})
