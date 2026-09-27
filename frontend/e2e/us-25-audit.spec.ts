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
    test.setTimeout(120_000) // до минуты ждём начала следующей минуты
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
    // «С 12:30 по 12:30» — ровно та минута, в которую уложились десять действий:
    // верхняя граница включает минуту целиком (ревью c0, 27.09.2026).
    await page.getByLabel('С момента').fill(местноеВремя(начало))
    await page.getByLabel('По момент').fill(местноеВремя(начало))
    await page.getByRole('button', { name: 'Найти' }).click()
    await expect(page.getByText('найдено 10')).toBeVisible()
    const строки = page.locator('tbody tr')
    await expect(строки).toHaveCount(10)
    await expect(строки.filter({ hasText: 'tech1' })).toHaveCount(10)
    await expect(строки.filter({ hasText: '/api/auth/me' })).toHaveCount(10)
  })

  // Каждый GET /api/audit сам пишет строку в журнал — без верхней границы
  // «Старее» сдвигал бы страницу и «найдено N» росло бы (ревью c0, 27.09.2026).
  test('US-25 сц. 1: листание не сдвигает журнал', async ({ page }) => {
    await page.goto('/admin/audit')
    const счёт = page.getByText(/найдено \d+/)
    await expect(счёт).toBeVisible()
    const было = await счёт.textContent()
    // Строку о первом запросе сервер дописывает не мгновенно: без паузы
    // «Старее» успевал раньше неё, и тест проходил и на сломанном экране.
    await page.waitForTimeout(1000)
    // Ждём ответ второй страницы, а не подпись «201–…»: подпись перерисовывается
    // по клику из старых данных, и проверка ниже проходила до прихода total.
    const вторая = page.waitForResponse(
      (r) => r.url().includes('/api/audit') && r.url().includes('offset=200'),
    )
    await page.getByRole('button', { name: 'Старее →' }).click()
    await вторая
    await expect(счёт).toHaveText(было!)
  })

  test('US-25 сц. 1: логин не шлёт запрос на каждую букву', async ({ page }) => {
    await page.goto('/admin/audit')
    await expect(page.getByText(/найдено \d+/)).toBeVisible()
    const запросы: string[] = []
    page.on('request', (r) => r.url().includes('/api/audit') && запросы.push(r.url()))
    await page.getByLabel('Логин').pressSequentially('tech1')
    await page.waitForTimeout(500)
    expect(запросы).toEqual([])
    await page.getByLabel('Логин').press('Enter')
    await expect.poll(() => запросы.length).toBe(1)
    expect(запросы[0]).toContain('login=tech1')
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
