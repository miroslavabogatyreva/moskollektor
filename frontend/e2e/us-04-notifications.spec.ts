// US-04 сц. 5 «Событие не пропадает, пока его не отработали» — docs/user-stories.md,
// MOS-238. Вкладка «Неквитированные» на экране заявок читает
// GET /api/notifications?acked=false (постранично, PAGE_SIZE=200), «Квитировать»
// дёргает POST /api/notifications/{id}/ack (backend/app/api/notifications.py).
//
// ВНИМАНИЕ: каждый прогон необратимо квитирует одно событие стенда — acked_at
// не возвращается в null, отмены нет. Событие выбирается САМОЕ СТАРОЕ из
// неквитированных (не первое в списке — LIST_SQL сортирует по reported_at DESC,
// то есть первое всегда самое новое), чтобы прогон стабильно съедал именно
// давние демонстрационные события, а не то, что интересно диспетчеру сейчас.
import { expect, test } from '@playwright/test'

interface Notification {
  id: number
  reported_at: string
}

test('US-04 сц. 5: событие не пропадает, пока его не отработали', async ({ page }) => {
  const listResp = await page.request.get('/api/notifications?acked=false&limit=1000')
  const { items: allItems, total: totalBefore } = (await listResp.json()) as {
    items: Notification[]
    total: number
  }
  const oldest = allItems.reduce((a, b) => (a.reported_at < b.reported_at ? a : b))

  await page.goto('/orders')
  await page.getByRole('tab', { name: 'Неквитированные' }).click()

  // «показано N из total» — total совпадает с тем, что только что отдал API.
  await expect(page.getByText(`из ${totalBefore}`)).toBeVisible()

  let row = page.locator(`[data-notification-id="${oldest.id}"]`)
  const showMore = page.getByRole('button', { name: 'Показать ещё' })
  for (let i = 0; (await row.count()) === 0 && i < 10; i++) {
    await showMore.click()
    row = page.locator(`[data-notification-id="${oldest.id}"]`)
  }
  await expect(row).toBeVisible()

  const [ackResponse] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/notifications/${oldest.id}/ack`) && r.request().method() === 'POST',
    ),
    row.getByRole('button', { name: 'Квитировать' }).click(),
  ])
  const ackBody = (await ackResponse.json()) as { acked_by: string | null; acked_at: string | null }
  expect(ackBody.acked_by).toBe(process.env.E2E_LOGIN ?? 'dispatcher1')
  expect(ackBody.acked_at).toBeTruthy()

  await expect(page.locator(`[data-notification-id="${oldest.id}"]`)).toHaveCount(0)

  // Событие правда квитировано, а не просто спрятано на экране — тот же ответ
  // API, из которого экран его теперь не увидит.
  const после = await page.request.get('/api/notifications?acked=false&limit=1000')
  const items = ((await после.json()) as { items: Notification[] }).items
  expect(items.some((n) => n.id === oldest.id)).toBe(false)
})
