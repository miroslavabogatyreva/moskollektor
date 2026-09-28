// MOS-130 (план 5.19), строки приёмки М-09, М-11: экран заявок не печатает код
// статуса, называет просрочку числом суток, а внутри одного срока ставит выше
// заявку с более срочным приоритетом. На стенде 28.09.2026 360 заявок, все OPEN,
// все со сроком в июне 2026 — просрочены на три месяца и выглядели как свежие.
import { expect, test } from '@playwright/test'

interface Order {
  id: number
  due_at: string
  status: string
  priority_code: string
}

const КОДЫ = /\b(OPEN|IN_PROCESS|COMPLETED|CANCELLED)\b/

test('MOS-130: внутри одного срока выше срочный приоритет', async ({ request }) => {
  const { items } = (await (await request.get('/api/orders?limit=1000')).json()) as {
    items: Order[]
  }
  expect(items.length).toBeGreaterThan(1)
  const нарушения = items.slice(1).filter((o, i) => {
    const пред = items[i]
    return o.due_at === пред.due_at && o.priority_code < пред.priority_code
  })
  expect(
    нарушения.map((o) => o.id),
    'заявки ниже менее срочной при том же сроке',
  ).toEqual([])
})

test('MOS-130: статус словом, просрочка числом суток', async ({ page }) => {
  const { items } = (await (await page.request.get('/api/orders?limit=1')).json()) as {
    items: Order[]
  }
  const первая = items[0]
  await page.goto('/orders')
  const строка = page.locator('tbody tr', { hasText: new RegExp(`^\\s*${первая.id}\\b`) }).first()
  await expect(строка).toBeVisible()
  await expect(page.locator('tbody')).not.toContainText(КОДЫ)
  const сутки = Math.floor((Date.now() - Date.parse(первая.due_at)) / 86_400_000)
  if (сутки >= 1) await expect(строка).toContainText(`просрочено на ${сутки} сут.`)
})
