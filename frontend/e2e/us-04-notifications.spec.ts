// US-04 сц. 5 «Событие не пропадает, пока его не отработали» — docs/user-stories.md,
// MOS-238. Вкладка «Неквитированные» на экране заявок читает
// GET /api/notifications?acked=false (постранично, PAGE_SIZE=200), «Квитировать»
// дёргает POST /api/notifications/{id}/ack (backend/app/api/notifications.py).
//
// ВНИМАНИЕ: каждый прогон необратимо квитирует одно событие стенда — acked_at
// не возвращается в null, отмены нет. Событие выбирается САМОЕ СТАРОЕ из
// неквитированных на момент прогона (не константа: проигрывание СМВУ льёт
// новые события непрерывно, и общее число растёт от прогона к прогону), чтобы
// стабильно съедать давние демонстрационные записи, а не то, что интересно
// диспетчеру сейчас.
//
// LIST_SQL сортирует по reported_at DESC, id DESC (notifications.py:73) — самое
// старое событие лежит на последней странице, а не в первых 200 по умолчанию.
// Поэтому: (1) «самое старое» ищем парой (reported_at, id), у соседних записей
// проигрывания reported_at совпадает; (2) итоговую проверку делаем по
// GET ?acked=true, а не «пропало из ?acked=false в первых 200» — та проверка
// прошла бы даже при неработающем квитировании, событие и так никогда не
// попадает в первую страницу.
import { expect, test } from '@playwright/test'

interface Notification {
  id: number
  reported_at: string
}

const старше = (a: Notification, b: Notification) =>
  a.reported_at !== b.reported_at ? a.reported_at < b.reported_at : a.id < b.id

test('US-04 сц. 5: событие не пропадает, пока его не отработали', async ({ page }) => {
  const listResp = await page.request.get('/api/notifications?acked=false&limit=1000')
  const { items: allItems, total: totalBefore } = (await listResp.json()) as {
    items: Notification[]
    total: number
  }
  const oldest = allItems.reduce((a, b) => (старше(a, b) ? a : b))

  await page.goto('/orders')
  await page.getByRole('tab', { name: 'Неквитированные' }).click()

  // «показано N из total» сразу после загрузки — total тот же, что только что
  // отдал API (живое число, не константа).
  await expect(page.getByText(`из ${totalBefore}`)).toBeVisible()

  // Докручиваем страницы до конца и проверяем, что подгрузка не теряет хвост:
  // число показанных строк должно сойтись с total, который экран видит сейчас.
  const showMore = page.getByRole('button', { name: 'Показать ещё' })
  for (let i = 0; (await showMore.isVisible().catch(() => false)) && i < 50; i++) {
    await showMore.click()
  }
  const строкаСчёта = await page.getByText(/показано \d+ из \d+/).textContent()
  const совпадение = строкаСчёта?.match(/показано (\d+) из (\d+)/)
  expect(совпадение?.[1]).toBe(совпадение?.[2])

  const row = page.locator(`[data-notification-id="${oldest.id}"]`)
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

  // Событие правда квитировано, а не просто спрятано на экране: смотрим в
  // acked=true, не в «нет в первых 200 acked=false» — та проверка была бы
  // истиной и без ack, самое старое событие туда всё равно не попадает.
  const ackedResp = await page.request.get('/api/notifications?acked=true&limit=1000')
  const ackedItems = ((await ackedResp.json()) as {
    items: (Notification & { acked_by: string | null })[]
  }).items
  const ackedRow = ackedItems.find((n) => n.id === oldest.id)
  expect(ackedRow?.acked_by).toBe(process.env.E2E_LOGIN ?? 'dispatcher1')
})
