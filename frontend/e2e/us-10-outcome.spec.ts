// US-10. Закрыть прогноз фактом — docs/user-stories.md, приёмка Ф-34, Ф-35, Ф-36,
// Ф-75, Ф-95. Исход — отдельно от решения (US-09): решение — что диспетчер сделал,
// когда прогноз пришёл; исход — чем прогноз кончился. Пишет его POST
// /api/forecasts/{id}/outcome (миграция 056), справочник — GET /api/dispatcher-decisions.
//
// ВНИМАНИЕ: сц. 5 необратимо добавляет строку в pred.forecast_outcome стенда.
// Исход ставим прогнозу САМОГО СТАРОГО дня журнала: его горизонт давно истёк,
// и живые прогнозы, которые интересны диспетчеру, тест не трогает. Берём первую
// строку журнала за этот день — она на первой странице, без листания.
import { expect, test, type Page } from '@playwright/test'

const DISPATCHER = process.env.E2E_LOGIN ?? 'dispatcher1'

interface Row {
  forecast_id: number
  computed_at: string
  outcome?: { outcome_name: string } | null
}

// День самого старого прогноза и первые строки журнала за этот день.
async function старыйДень(page: Page): Promise<{ день: string; строки: Row[] }> {
  const { total } = (await (await page.request.get('/api/forecasts?limit=1')).json()) as {
    total: number
  }
  const { items } = (await (
    await page.request.get(`/api/forecasts?limit=1&offset=${total - 1}`)
  ).json()) as { items: Row[] }
  const день = new Date(items[0].computed_at).toLocaleDateString('sv-SE')
  const r = await page.request.get(`/api/forecasts?from=${день}&to=${день}&limit=5`)
  return { день, строки: ((await r.json()) as { items: Row[] }).items }
}

async function журналЗаДень(page: Page, день: string) {
  await page.goto('/log')
  const загрузка = page.waitForResponse(
    (r) => r.url().includes('/api/forecasts?') && r.url().includes(`to=${день}`),
  )
  await page.getByLabel('С даты').fill(день)
  await page.getByLabel('По дату').fill(день)
  await загрузка
}

test('US-10 сц. 1, 2, 3, 5: три исхода, у ложной причина из пяти, прогноз с исходом в журнале', async ({
  page,
}) => {
  const { день, строки } = await старыйДень(page)
  const id = строки[0].forecast_id
  const posts: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/outcome')) posts.push(r.url())
  })

  await page.goto(`/forecasts/${id}`)
  await page.getByRole('button', { name: 'Отметить исход' }).click()
  const диалог = page.getByRole('dialog', { name: 'Исход прогноза' })
  await expect(диалог).toBeVisible()

  // Сц. 1: ровно три исхода.
  const исход = диалог.getByLabel('Исход')
  await expect(исход.locator('option:not([value=""])')).toHaveText([
    'подтвердилось',
    'ложная',
    'не проверяли',
  ])

  // Сц. 2: «ложная» без причины не сохраняется — кнопка неактивна, запроса нет.
  await исход.selectOption({ label: 'ложная' })
  const сохранить = диалог.getByRole('button', { name: 'Сохранить' })
  await expect(сохранить).toBeDisabled()
  await expect(диалог.getByText('Выберите причину')).toBeVisible()
  await сохранить.click({ force: true })
  expect(posts).toHaveLength(0)
  // Сервер держит то же правило сам: 422, а не строка без причины.
  const безПричины = await page.request.post(`/api/forecasts/${id}/outcome`, {
    data: { outcome_code: 'false_alarm' },
  })
  expect(безПричины.status()).toBe(422)

  // Сц. 3: ровно пять причин.
  await expect(диалог.getByLabel('Причина').locator('option:not([value=""])')).toHaveText([
    'Отказ датчика',
    'Плановые работы',
    'Погода',
    'Ремонт соседней сети',
    'Неизвестно',
  ])

  // Сц. 5: исход сохранён — прогноз остаётся в журнале за свой период, с исходом.
  await исход.selectOption({ label: 'подтвердилось' })
  const сохранено = page.waitForResponse(
    (r) => r.url().endsWith(`/api/forecasts/${id}/outcome`) && r.request().method() === 'POST',
  )
  await сохранить.click()
  const ответ = (await (await сохранено).json()) as { decided_by: string }
  expect(ответ.decided_by).toBe(DISPATCHER)
  await expect(page.getByTestId('outcome')).toContainText('подтвердилось')
  await expect(page.getByTestId('outcome')).toContainText(DISPATCHER)

  await журналЗаДень(page, день)
  const строка = page.locator(`main table tbody tr[data-forecast-id="${id}"]`)
  await expect(строка, 'прогноз с исходом остался в журнале').toBeVisible()
  await expect(строка.getByTestId('outcome-cell')).toContainText('подтвердилось')
})

test('US-10 сц. 4: система не ставит исход сама', async ({ page }) => {
  const { день, строки } = await старыйДень(page)
  // Прогноз, которому исход никто не отмечал: горизонт истёк давно.
  const безИсхода = строки.find((r) => !r.outcome)
  expect(безИсхода, 'за старый день есть прогноз без исхода').toBeTruthy()

  await журналЗаДень(page, день)
  const ячейка = page
    .locator(`main table tbody tr[data-forecast-id="${безИсхода!.forecast_id}"]`)
    .getByTestId('outcome-cell')
  await expect(ячейка).toHaveText('горизонт истёк')
})
