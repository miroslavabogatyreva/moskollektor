// US-25 «Журнал действий пользователей» — docs/user-stories.md, MOS-124 (план 5.14),
// приёмка НФ-77, Ф-53. Экран /admin/audit читает GET /api/audit?from=&to=&login=.
import { expect, test } from '@playwright/test'

// Значение для <input type="datetime-local"> в поясе браузера, с точностью до минуты.
function местноеВремя(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

test.describe('под администратором', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': 'admin1' } })

  test('US-25 сц. 1: каждое действие — одна запись', async ({ page }) => {
    // Начало периода — начало следующей минуты: поле даты режет до минуты, и
    // чужие действия tech1 из текущей минуты в период не попадут.
    const начало = new Date(Math.ceil((Date.now() + 1) / 60_000) * 60_000)
    await page.waitForTimeout(начало.getTime() - Date.now() + 500)
    // Десять действий техника: заголовок X-User-Login на каждый запрос,
    // промежуточный слой пишет строку в audit.user_action на каждый.
    for (let i = 0; i < 10; i++) {
      const r = await page.request.get('/api/auth/me', { headers: { 'X-User-Login': 'tech1' } })
      expect(r.status()).toBe(200)
    }

    await page.goto('/dashboard')
    await page
      .getByRole('navigation', { name: 'Разделы' })
      .getByRole('link', { name: 'Журнал действий' })
      .click()
    await expect(page).toHaveURL(/\/admin\/audit$/)
    for (const h of ['Время', 'Логин', 'Метод', 'Путь', 'Код ответа']) {
      await expect(page.getByRole('columnheader', { name: h })).toBeVisible()
    }
    await page.getByLabel('Логин').fill('tech1')
    await page.getByLabel('С момента').fill(местноеВремя(начало))
    await expect(page.getByText('найдено 10')).toBeVisible()
    const строки = page.locator('tbody tr')
    await expect(строки).toHaveCount(10)
    await expect(строки.filter({ hasText: 'tech1' })).toHaveCount(10)
    await expect(строки.filter({ hasText: '/api/auth/me' })).toHaveCount(10)
  })
})

test('US-25 сц. 2: журнал закрыт от остальных', async ({ page }) => {
  const r = await page.request.get('/api/audit')
  expect(r.status()).toBe(403)
  await page.goto('/dashboard')
  await expect(
    page
      .getByRole('navigation', { name: 'Разделы' })
      .getByRole('link', { name: 'Журнал действий' }),
  ).toHaveCount(0)
  await page.goto('/admin/audit')
  await expect(page.getByText('Журнал действий доступен только администратору')).toBeVisible()
})
