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

  // Диспетчер делает действия из сценария тем же путём, каким их делает экран:
  // вход POST /api/auth/login (дальше личность едет кукой mk_session, а не
  // заголовком), 4 решения POST /api/forecasts/{id}/feedback, выход
  // POST /api/auth/logout. Запросы идут напрямую, без страниц: открытая страница
  // сама шлёт GET (риски, уведомления, /api/auth/me), и каждый GET — тоже
  // запись журнала (НФ-77 считает просмотр раздела действием), так что в журнале
  // было бы больше, чем действий в сценарии.
  //
  // ИСХОДОВ ПОКА НЕТ: US-10 (MOS-195, «закрыть прогноз фактом») делается
  // параллельно. После неё сюда добавляются 4 исхода, и ДЕЙСТВИЙ станет 10,
  // как в сценарии; сейчас их 6.
  //
  // ВНИМАНИЕ: решения на стенде необратимы (pred.feedback, решение 4f 27.09.2026).
  // Ставим их на 4 САМЫХ СТАРЫХ прогноза, как e2e/us-09-decision.spec.ts.
  //
  // Диспетчер — disp2, а не dispatcher1: под dispatcher1 по умолчанию ходят все
  // остальные спеки и соседние сессии, и 27.09.2026 первый прогон поймал в своей
  // минуте 22 чужих GET dispatcher1 («найдено 28» при 6 действиях). disp2 трогает
  // только US-16. Его пароля нет в GET /api/auth/info, он записан в db/seed/rbac.sql.
  test('US-25 сц. 1: каждое действие — одна запись', async ({ page, browser }) => {
    const диспетчер = {
      login: process.env.E2E_US25_LOGIN ?? 'disp2',
      password: process.env.E2E_US25_PASSWORD ?? 'disp2123123',
    }

    // Четыре самых старых прогноза в районе диспетчера — до входа: этот просмотр
    // тоже запись журнала, но в период между входом и выходом он не попадёт.
    const поиск = await browser.newContext({
      baseURL: test.info().project.use.baseURL,
      extraHTTPHeaders: { 'X-User-Login': диспетчер.login },
    })
    const первая = await поиск.request.get('/api/forecasts?limit=1')
    const { total } = (await первая.json()) as { total: number }
    expect(total).toBeGreaterThanOrEqual(4)
    const старые = await поиск.request.get(`/api/forecasts?limit=4&offset=${total - 4}`)
    const прогнозы = ((await старые.json()) as { items: { forecast_id: number }[] }).items.map(
      (f) => f.forecast_id,
    )
    expect(прогнозы).toHaveLength(4)
    await поиск.close()

    const начало = new Date()
    const сессия = await browser.newContext({ baseURL: test.info().project.use.baseURL })
    const действия: { method: string; path: string; status: number }[] = []
    const вход = await сессия.request.post('/api/auth/login', {
      data: { login: диспетчер.login, password: диспетчер.password },
    })
    действия.push({ method: 'POST', path: '/api/auth/login', status: вход.status() })
    for (const id of прогнозы) {
      const r = await сессия.request.post(`/api/forecasts/${id}/feedback`, {
        data: { decision_code: 'monitor', comment: 'E2E US-25 сц. 1' },
      })
      действия.push({ method: 'POST', path: `/api/forecasts/${id}/feedback`, status: r.status() })
    }
    const выход = await сессия.request.post('/api/auth/logout')
    действия.push({ method: 'POST', path: '/api/auth/logout', status: выход.status() })
    const конец = new Date()
    await сессия.close()
    expect(действия.map((д) => д.status)).toEqual([200, 201, 201, 201, 201, 204])

    // Администратор открывает журнал за этот период. Поле даты режет до минуты,
    // часы стенда идут впереди облачных примерно на секунду (замер 27.09.2026),
    // поэтому края периода взяты с запасом в 5 секунд.
    await page.goto('/dashboard')
    await page
      .getByRole('navigation', { name: 'Разделы' })
      .getByRole('link', { name: 'Журнал действий' })
      .click()
    await expect(page).toHaveURL(/\/admin\/audit$/)
    for (const h of ['Время', 'Логин', 'Метод', 'Путь', 'Код ответа']) {
      await expect(page.getByRole('columnheader', { name: h })).toBeVisible()
    }
    await page.getByLabel('Логин').fill(диспетчер.login)
    await page.getByLabel('С момента').fill(местноеВремя(new Date(начало.getTime() - 5_000)))
    await page.getByLabel('По момент').fill(местноеВремя(new Date(конец.getTime() + 5_000)))
    await page.getByRole('button', { name: 'Найти' }).click()
    await expect(page.getByText(/найдено \d+/)).toBeVisible()
    // Каждое действие — ровно одна строка, у каждой учётная запись, время
    // и объект: путь с номером прогноза.
    const строки = page.locator('tbody tr')
    for (const д of действия) {
      const строка = строки.filter({
        hasText: new RegExp(`${д.method}\\s*${д.path}\\s*${д.status}`),
      })
      await expect(строка, д.path).toHaveCount(1)
      await expect(строка).toContainText(диспетчер.login)
      await expect(строка.locator('td').first()).toHaveText(/\d{2}\.\d{2}\.\d{4},? \d{2}:\d{2}/)
    }

    // Ровно столько записей, сколько действий, между входом и выходом по часам
    // самого журнала. Экран считает целыми минутами, и в них попадают чужие
    // запросы под тем же логином: 27.09.2026 соседняя сессия ходила под disp2
    // через 8 секунд после выхода. Здесь окно — секунды сценария, без округления.
    const журнал = await page.request.get(
      `/api/audit?login=${диспетчер.login}&from=${new Date(начало.getTime() - 5_000).toISOString()}&limit=1000`,
    )
    const { items } = (await журнал.json()) as {
      items: { occurred_at: string; method: string; path: string; status_code: number }[]
    }
    const поПорядку = [...items].reverse()
    const i = поПорядку.findIndex((a) => a.path === '/api/auth/login' && a.status_code === 200)
    const j = поПорядку.findIndex((a, k) => k > i && a.path === '/api/auth/logout')
    expect(i, 'вход записан').toBeGreaterThanOrEqual(0)
    expect(j, 'выход записан после входа').toBeGreaterThan(i)
    expect(
      поПорядку
        .slice(i, j + 1)
        .map((a) => ({ method: a.method, path: a.path, status: a.status_code })),
    ).toEqual(действия)
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

test.describe('под диспетчером ОДС', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })

  test('US-25 сц. 2: журнал закрыт от остальных', async ({ page }) => {
    const r = await page.request.get('/api/audit')
    expect(r.status()).toBe(403)
    // Пункты администратора Nav дорисовывает по ответу /api/auth/me: без ожидания
    // проверка «пункта нет» прошла бы и до ответа, на любой роли.
    const кто = page.waitForResponse((r) => r.url().endsWith('/api/auth/me'))
    await page.goto('/dashboard')
    expect(((await (await кто).json()) as { roles: string[] }).roles).toEqual(['ods_dispatcher'])
    await expect(
      page
        .getByRole('navigation', { name: 'Разделы' })
        .getByRole('link', { name: 'Журнал действий' }),
    ).toHaveCount(0)
    await page.goto('/admin/audit')
    await expect(page.getByText('Журнал действий доступен только администратору')).toBeVisible()
  })
})
