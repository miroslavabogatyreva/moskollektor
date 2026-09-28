// Плашка «Высокий риск отказа датчика» называет событие и срок словами: «вероятность 89 %»
// без слов «чего» была непонятна (Слава, 28.09.2026). Только чтение: уведомление
// не квитируем — кнопку «Принял» не трогаем.
import { expect, test } from '@playwright/test'
import { свойБандл } from './helpers/sensor-mock'

test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })
test.beforeEach(({ page }) => свойБандл(page))

test('плашка: «отказ датчика на участке в ближайшие N ч: вероятность …»', async ({ page }) => {
  await page.goto('/map')
  const плашка = page.getByRole('status', { name: 'Уведомление о прогнозе' })
  await expect(плашка, 'на стенде есть неквитированное уведомление').toBeVisible()
  await expect(плашка).toContainText(
    /отказ датчика на участке в ближайшие \d+ ч: вероятность \d+ %/,
  )
  await expect(плашка).toContainText('Высокий риск отказа датчика')
  await expect(плашка).not.toContainText(/горизонт \d+ ч/)
})
