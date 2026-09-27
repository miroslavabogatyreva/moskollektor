// US-09 «Зафиксировать решение по прогнозу» — docs/user-stories.md, MOS-55 (план 5.8),
// приёмка Ф-92. Кнопка «Решение диспетчера» в карточке прогноза открывает
// VerdictDialog, «Сохранить» дёргает POST /api/forecasts/{id}/feedback
// (backend/app/api/routes.py), справочники отдаёт GET /api/dispatcher-decisions.
//
// ВНИМАНИЕ: сц. 1 необратимо добавляет строку в pred.feedback стенда — история
// решений входит в журнал и не удаляется (решение оркестратора 4f, 27.09.2026).
// Решение ставим на САМЫЙ СТАРЫЙ прогноз: GET /api/forecasts сортирует по
// started_at DESC, значит последняя строка по offset=total-1 — самая старая,
// и живые прогнозы, которые интересны диспетчеру, тест не трогает. Повторный
// прогон добавит ещё одну строку тому же прогнозу, карточка покажет последнюю.
import { expect, test, type Page } from '@playwright/test'

const DISPATCHER = process.env.E2E_LOGIN ?? 'dispatcher1'

async function oldestForecastId(page: Page): Promise<number> {
  const first = await page.request.get('/api/forecasts?limit=1')
  const { total } = (await first.json()) as { total: number }
  expect(total).toBeGreaterThan(0)
  const last = await page.request.get(`/api/forecasts?limit=1&offset=${total - 1}`)
  const { items } = (await last.json()) as { items: { forecast_id: number }[] }
  return items[0].forecast_id
}

test('US-09 сц. 1, 2, 3, 4: решение из справочника, без выбора не сохранить, текст — только комментарий', async ({
  page,
}) => {
  const id = await oldestForecastId(page)
  const posts: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/feedback')) posts.push(r.url())
  })

  await page.goto(`/forecasts/${id}`)
  await page.getByRole('button', { name: 'Решение диспетчера' }).click()
  const dialog = page.getByRole('dialog', { name: 'Решение диспетчера' })
  await expect(dialog).toBeVisible()

  // Сц. 2: решение не выбрано — «Сохранить» неактивна, подсказка под списком,
  // и ни одного POST в сети.
  const save = dialog.getByRole('button', { name: 'Сохранить' })
  await expect(save).toBeDisabled()
  await expect(dialog.getByText('Выберите решение')).toBeVisible()
  await save.click({ force: true })
  expect(posts).toHaveLength(0)

  // Сц. 3: решение — только из закрытого списка из четырёх, свободный текст —
  // только в комментарии (textarea), своего пункта «другое» в списке нет.
  const decision = dialog.getByLabel('Решение')
  await expect(decision.locator('option:not([value=""])')).toHaveText([
    'Выезд бригады',
    'Направление бригады на проверку',
    'Мониторинг ситуации',
    'Ложное срабатывание',
  ])

  // «Ложное срабатывание» требует причину из справочника: пока её нет,
  // сохранить нельзя. Причин ровно пять (039_feedback_reason_five.sql).
  await decision.selectOption({ label: 'Ложное срабатывание' })
  const reason = dialog.getByLabel('Причина')
  await expect(reason.locator('option:not([value=""])')).toHaveCount(5)
  await expect(save).toBeDisabled()

  // Сохраняем не ложное: вердикт 0 портил бы статистику ложных на стенде.
  await decision.selectOption({ label: 'Мониторинг ситуации' })
  await expect(reason).toHaveCount(0)
  const comment = `E2E US-09 ${new Date().toISOString()}`
  await dialog.getByLabel('Комментарий').fill(comment)
  await dialog.getByLabel('Проверено по внешним источникам').check()
  await expect(save).toBeEnabled()

  const [resp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/forecasts/${id}/feedback`) && r.request().method() === 'POST',
    ),
    save.click(),
  ])
  expect(resp.status()).toBe(201)
  const saved = (await resp.json()) as {
    decision_code: string
    decided_by: string
    comment: string
    verified_externally: boolean
  }
  expect(saved.decision_code).toBe('monitor')
  expect(saved.decided_by).toBe(DISPATCHER)
  expect(saved.comment).toBe(comment)
  expect(saved.verified_externally).toBe(true)

  // Сц. 1: у прогноза появилось решение — в карточке, с автором.
  await expect(dialog).toBeHidden()
  const last = page.getByTestId('last-decision')
  await expect(last).toContainText('Мониторинг ситуации')
  await expect(last).toContainText(DISPATCHER)
  await expect(last).toContainText('проверено по внешним источникам')
  // Фокус вернулся на кнопку, а не потерялся на body вместе со снятым диалогом.
  await expect(page.getByRole('button', { name: 'Решение диспетчера' })).toBeFocused()

  // Сц. 4: другой диспетчер (ОДС) видит то же решение, кто и когда.
  const other = await page.request.get(`/api/forecasts/${id}`, {
    headers: { 'X-User-Login': 'ods1' },
  })
  const detail = (await other.json()) as {
    decision: {
      decision_code: string
      decided_by: string
      decided_at: string
      comment: string
    } | null
  }
  expect(detail.decision?.decision_code).toBe('monitor')
  expect(detail.decision?.decided_by).toBe(DISPATCHER)
  expect(detail.decision?.comment).toBe(comment)
  expect(detail.decision?.decided_at).toBeTruthy()

  // Сц. 1: в журнале действий (НФ-77) — запись о решении с учётной записью и временем.
  const журнал = await page.request.get(`/api/audit?login=${DISPATCHER}&limit=50`, {
    headers: { 'X-User-Login': 'admin1' },
  })
  const { items } = (await журнал.json()) as {
    items: { path: string; method: string; login: string; occurred_at: string }[]
  }
  const запись = items.find(
    (a) => a.method === 'POST' && a.path === `/api/forecasts/${id}/feedback`,
  )
  expect(запись, 'решение записано в журнал действий').toBeTruthy()
  expect(запись!.login).toBe(DISPATCHER)
  expect(
    Math.abs(Date.parse(запись!.occurred_at) - Date.parse(detail.decision!.decided_at)),
  ).toBeLessThan(60_000)

  // Сц. 4 глазами: другой диспетчер ОДС открывает тот же прогноз и видит решение,
  // кто и когда его принял.
  const ods = await page
    .context()
    .browser()!
    .newContext({
      baseURL: test.info().project.use.baseURL,
      extraHTTPHeaders: { 'X-User-Login': 'ods1' },
    })
  const odsPage = await ods.newPage()
  await odsPage.goto(`/forecasts/${id}`)
  const уOds = odsPage.getByTestId('last-decision')
  await expect(уOds).toContainText('Мониторинг ситуации')
  await expect(уOds).toContainText(DISPATCHER)
  await expect(уOds).toContainText(/\d\d\.\d\d\.\d{4} \d\d:\d\d/)
  await ods.close()
})

test('US-09 сц. 2 на сервере: POST без решения — 422, ложное без причины — 422, техник — 403', async ({
  page,
}) => {
  const id = await oldestForecastId(page)
  const url = `/api/forecasts/${id}/feedback`

  const noDecision = await page.request.post(url, { data: { comment: 'без решения' } })
  expect(noDecision.status()).toBe(422)

  const unknown = await page.request.post(url, { data: { decision_code: 'другое' } })
  expect(unknown.status()).toBe(422)

  const falseNoReason = await page.request.post(url, { data: { decision_code: 'false_alarm' } })
  expect(falseNoReason.status()).toBe(422)

  const reasonNotFalse = await page.request.post(url, {
    data: { decision_code: 'monitor', reason_code: 'weather' },
  })
  expect(reasonNotFalse.status()).toBe(422)

  const longComment = await page.request.post(url, {
    data: { decision_code: 'monitor', comment: 'я'.repeat(2001) },
  })
  expect(longComment.status()).toBe(422)

  const tech = await page.request.post(url, {
    data: { decision_code: 'monitor' },
    headers: { 'X-User-Login': 'tech1' },
  })
  expect(tech.status()).toBe(403)
})

test('US-09: технику кнопку решения не показываем', async ({ browser }) => {
  const ctx = await browser.newContext({ extraHTTPHeaders: { 'X-User-Login': 'tech1' } })
  const page = await ctx.newPage()
  const id = await oldestForecastId(page)
  await page.goto(`/forecasts/${id}`)
  // Якорь: права уже проверены, а не «ещё не ответили» (data-can-decide ставит ответ /api/auth/me).
  await expect(page.getByTestId('last-decision')).toHaveAttribute('data-can-decide', 'false')
  await expect(page.getByRole('button', { name: 'Решение диспетчера' })).toHaveCount(0)
  await ctx.close()
})
