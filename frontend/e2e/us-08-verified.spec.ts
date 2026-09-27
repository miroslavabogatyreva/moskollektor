// US-08. Отметить, чем проверил прогноз — docs/user-stories.md, приёмка Ф-91.
//
// ВНИМАНИЕ: тест необратимо добавляет решение в pred.feedback стенда самому
// свежему прогнозу журнала — история решений не удаляется. Повторный прогон
// добавит ещё одно, карточка и журнал покажут последнее.
import { expect, test } from '@playwright/test'

const DISPATCHER = process.env.E2E_LOGIN ?? 'dispatcher1'

test('US-08 сц. 1: отметка о проверке сохраняется', async ({ page }) => {
  // Журнал за сегодня, первая строка — самый свежий прогноз.
  await page.goto('/log')
  const строка = page.locator('main table tbody tr').first()
  await expect(строка).toBeVisible()
  await строка.click()
  await expect(page).toHaveURL(/\/forecasts\/\d+$/)
  const id = page.url().match(/\/forecasts\/(\d+)$/)![1]

  await page.getByRole('button', { name: 'Решение диспетчера' }).click()
  const диалог = page.getByRole('dialog', { name: 'Решение диспетчера' })
  await диалог.getByLabel('Решение').selectOption({ label: 'Мониторинг ситуации' })
  await диалог.getByLabel('Проверено по внешним источникам').check()
  const сохранено = page.waitForResponse(
    (r) => r.url().endsWith(`/api/forecasts/${id}/feedback`) && r.request().method() === 'POST',
  )
  await диалог.getByRole('button', { name: 'Сохранить' }).click()
  const ответ = (await (await сохранено).json()) as { decided_at: string; decided_by: string }
  expect(ответ.decided_by).toBe(DISPATCHER)

  // В карточке: отметка, учётная запись и время.
  const решение = page.getByTestId('last-decision')
  await expect(решение).toContainText('проверено по внешним источникам')
  await expect(решение).toContainText(DISPATCHER)

  // В журнале прогнозов — та же отметка у этой строки.
  await page.goBack()
  await expect(page).toHaveURL(/\/log/)
  const вЖурнале = page.locator(`main table tbody tr[data-forecast-id="${id}"]`)
  await expect(вЖурнале, 'строка прогноза в журнале').toBeVisible()
  await expect(вЖурнале).toContainText('проверено по внешним источникам')
  await expect(вЖурнале).toContainText(DISPATCHER)
  await expect(вЖурнале).toContainText(/\d\d\.\d\d\.\d{4}/)
})
