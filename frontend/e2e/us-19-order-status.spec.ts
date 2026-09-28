// US-19. Статус заявки из системы учёта — docs/user-stories.md, приёмка Ф-87.
// Статусы подаёт эмулятор хелпдеска (backend/app/api/helpdesk_emu.py): worker раз
// в 10 минут спрашивает его и каждую смену статуса пишет строкой в
// maint.notification_status_log (миграция 058) с источником «система учёта».
// Цикл эмулятора «принята → назначена бригада → в работе → выполнена» идёт
// от заведения заявки у нас, поэтому статус меняется сам, без участия теста.
//
// Сц. 2 («система учёта недоступна») здесь нет: эмулятор живёт в контейнере api,
// и остановить его, не положив стенд, нельзя.
import { expect, test, type APIRequestContext } from '@playwright/test'

interface Строка {
  changed_at: string
  status: string
  assignee: string | null
  source: string
}
interface Заявка {
  id: number
  external_status: string | null
  status_history: Строка[]
}

// Заявка, у которой система учёта уже хотя бы раз сменила статус: в истории две
// строки и больше. Молодые заявки — в конце списка (он идёт по сроку), с них и начинаем.
async function заявкаСоСменой(request: APIRequestContext): Promise<Заявка> {
  const { items } = (await (await request.get('/api/orders?limit=1000')).json()) as {
    items: { id: number }[]
  }
  // Статус меняется только у незакрытых молодых заявок: 60 последних хватает с запасом.
  for (const { id } of items.reverse().slice(0, 60)) {
    const з = (await (await request.get(`/api/orders/${id}`)).json()) as Заявка
    if ((з.status_history ?? []).length >= 2) return з
  }
  throw new Error(
    'ни у одной заявки система учёта ещё не сменила статус: после выкладки 058 подождать час',
  )
}

test('US-19 сц. 1: статус пришёл извне', async ({ page }) => {
  test.setTimeout(120_000)
  const з = await заявкаСоСменой(page.request)
  const последняя = з.status_history[з.status_history.length - 1]
  expect(последняя.status, 'последняя строка истории — текущий статус').toBe(з.external_status)
  expect(new Set(з.status_history.map((с) => с.source))).toEqual(new Set(['order_system']))

  await page.goto(`/orders/${з.id}`)
  await expect(page.getByText('Статус в системе учёта')).toBeVisible()
  await expect(page.getByText(з.external_status!, { exact: false }).first()).toBeVisible()

  const история = page.getByTestId('order-history')
  await expect(история.getByRole('heading', { name: 'История заявки' })).toBeVisible()
  const строки = история.locator('li')
  await expect(строки).toHaveCount(з.status_history.length)
  for (let i = 0; i < з.status_history.length; i++) {
    await expect(строки.nth(i)).toContainText(`статус «${з.status_history[i].status}»`)
    await expect(строки.nth(i)).toContainText('получен из системы учёта')
  }
})
