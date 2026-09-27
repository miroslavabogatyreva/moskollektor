// US-04 сц. 5 «Событие не пропадает, пока его не отработали» — docs/user-stories.md,
// MOS-238. Вкладка «Неквитированные» на экране заявок читает
// GET /api/notifications?acked=false, «Квитировать» дёргает
// POST /api/notifications/{id}/ack (backend/app/api/notifications.py).
import { expect, test } from '@playwright/test'

test('US-04 сц. 5: событие не пропадает, пока его не отработали', async ({ page }) => {
  await page.goto('/orders')
  await page.getByRole('tab', { name: 'Неквитированные' }).click()

  const row = page.getByRole('row').filter({ hasNot: page.getByRole('columnheader') }).first()
  await expect(row).toBeVisible()
  const notificationId = await row.getAttribute('data-notification-id')
  expect(notificationId).not.toBeNull()

  await row.getByRole('button', { name: 'Квитировать' }).click()
  await expect(page.locator(`[data-notification-id="${notificationId}"]`)).toHaveCount(0)

  // Событие правда квитировано, а не просто спрятано на экране — тот же ответ
  // API, из которого экран его теперь не увидит.
  const после = await page.request.get('/api/notifications?acked=false')
  const items = ((await после.json()) as { items: { id: number }[] }).items
  expect(items.some((n) => n.id === Number(notificationId))).toBe(false)
})
