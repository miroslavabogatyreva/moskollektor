// US-13. Участок в работах — docs/user-stories.md, приёмка Ф-64, Ф-10.
//
// Реестра нарядов у заказчика для нас нет: наряд открывает эмулятор —
// POST /api/permits под admin1 (право permits.write, миграция 055), — и тест
// закрывает его в конце, чтобы участок не остался «в работах».
//
// Сц. 2 «Заявка не создаётся» проверяет тест расчёта
// backend/tests/test_orders_permit.py: на стенде с 22.09.2026 расчёт не заводит
// заявок вовсе (модель не открывает новых предупреждений), и E2E «заявки нет»
// проходил бы впустую.
import { expect, test } from '@playwright/test'

const АДМИН = { 'X-User-Login': 'admin1' }

test('US-13 сц. 1: пометка «в работах»', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    risk_rank: number
  }[]
  const участок = риски.find((r) => r.risk_rank === 1)!.section_id
  const сейчас = Date.now()
  const r = await page.request.post('/api/permits', {
    headers: АДМИН,
    data: {
      section_id: участок,
      valid_from: new Date(сейчас - 3600_000).toISOString(),
      valid_to: new Date(сейчас + 2 * 3600_000).toISOString(),
      work_description: 'E2E US-13: замена кабеля связи',
    },
  })
  expect(r.status(), await r.text()).toBe(201)
  const наряд = (await r.json()) as { id: number; number: string; valid_to: string }
  try {
    await page.goto(`/objects/${участок}`)
    // Пометку ищем по номеру своего наряда: наряд прошлого прогона, который не успели
    // закрыть (27.09.2026 закрытие попало на 502 во время выкладки), дал бы вторую.
    const пометка = page.getByTestId('in-works').filter({ hasText: наряд.number })
    await expect(пометка).toContainText('Объект в работах')
    // Срок наряда — дата и время окончания.
    await expect(пометка).toContainText(/до \d\d\.\d\d\.\d{4} \d\d:\d\d/)
  } finally {
    await page.request.post(`/api/permits/${наряд.id}/close`, { headers: АДМИН })
  }
  // Закрытый наряд пометку снимает.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Уровень риска' })).toBeVisible()
  await expect(page.getByTestId('in-works').filter({ hasText: наряд.number })).toHaveCount(0)
})
