// Поиск заявки по номеру на экране «Заявки» (28.09.2026): id целиком или часть номера
// уведомления AF…/заказа AW…. Против стенда, только чтение (GET), вход заголовком.
import { expect, test } from '@playwright/test'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' } })

test('поиск по номеру заявки: id и часть номера AF находят одну и ту же заявку', async ({
  page,
  request,
}) => {
  const список = await request.get('/api/orders?limit=1')
  expect(список.status()).toBe(200)
  const { items, total } = (await список.json()) as { items: { id: number }[]; total: number }
  expect(total, 'у disp2 есть заявки').toBeGreaterThan(1)
  const id = items[0].id
  const карточка = (await (await request.get(`/api/orders/${id}`)).json()) as {
    notification_no: string
  }

  await page.goto('/orders')
  const поле = page.getByLabel('Номер заявки')
  const строки = page.locator('#orders-panel-orders table tbody tr')
  const число = page.getByTestId('orders-count')

  await поле.fill(String(id))
  await expect(число).toHaveText('найдено заявок: 1')
  await expect(строки).toHaveCount(1)
  await expect(строки.first().locator('td').first()).toHaveText(String(id))

  // Хвост номера уведомления в нижнем регистре — та же заявка.
  await поле.fill(карточка.notification_no.slice(-7).toLowerCase())
  await expect(строки.first().locator('td').first()).toHaveText(String(id))

  await поле.fill('')
  await expect(число).toHaveText(`заявок: ${total}`)
})
