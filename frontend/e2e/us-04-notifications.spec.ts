// US-04 сц. 5 «Событие не пропадает, пока его не отработали» — docs/user-stories.md,
// MOS-238. Вкладка «Неквитированные» на экране заявок читает
// GET /api/notifications?acked=false (постранично, PAGE_SIZE=200), «Квитировать»
// дёргает POST /api/notifications/{id}/ack (backend/app/api/notifications.py).
//
// ВНИМАНИЕ: каждый прогон необратимо квитирует одно событие стенда — acked_at
// не возвращается в null, отмены нет. Событие выбирается САМОЕ СТАРОЕ из
// неквитированных на момент прогона (не константа: проигрывание СМВУ льёт
// новые события непрерывно, и общее число растёт от прогона к прогону), чтобы
// стабильно съедать давние демонстрационные записи, а не то, что интересно
// диспетчеру сейчас.
//
// LIST_SQL сортирует по reported_at DESC, id DESC (notifications.py:73) — самое
// старое событие лежит на последней странице, а не в первых 200 по умолчанию.
// Поэтому: (1) «самое старое» ищем парой (reported_at, id), у соседних записей
// проигрывания reported_at совпадает; (2) итоговую проверку делаем по
// GET ?acked=true, а не «пропало из ?acked=false в первых 200» — та проверка
// прошла бы даже при неработающем квитировании, событие и так никогда не
// попадает в первую страницу.
import { expect, test } from '@playwright/test'

interface Notification {
  id: number
  reported_at: string
}

const старше = (a: Notification, b: Notification) =>
  a.reported_at !== b.reported_at ? a.reported_at < b.reported_at : a.id < b.id

test('US-04 сц. 5: событие не пропадает, пока его не отработали', async ({ page }) => {
  const listResp = await page.request.get('/api/notifications?acked=false&limit=1000')
  const { items: allItems, total: totalBefore } = (await listResp.json()) as {
    items: Notification[]
    total: number
  }
  const oldest = allItems.reduce((a, b) => (старше(a, b) ? a : b))

  await page.goto('/orders')
  await page.getByRole('tab', { name: 'Неквитированные' }).click()

  // «показано N из total» сразу после загрузки — total тот же, что только что
  // отдал API (живое число, не константа).
  await expect(page.getByText(`из ${totalBefore}`)).toBeVisible()

  // Докручиваем страницы до конца и проверяем, что подгрузка не теряет хвост:
  // число показанных строк должно сойтись с total, который экран видит сейчас.
  // click({timeout}) сам ждёт появления кнопки — isVisible() без ожидания
  // на первой же проверке иногда успевает выстрелить раньше рендера страницы.
  const showMore = page.getByRole('button', { name: 'Показать ещё' })
  for (let i = 0; i < 50; i++) {
    const clicked = await showMore
      .click({ timeout: 2000 })
      .then(() => true)
      .catch(() => false)
    if (!clicked) break
  }
  const строкаСчёта = await page.getByText(/показано \d+ из \d+/).textContent()
  const совпадение = строкаСчёта?.match(/показано (\d+) из (\d+)/)
  expect(совпадение?.[1]).toBe(совпадение?.[2])

  const row = page.locator(`[data-notification-id="${oldest.id}"]`)
  await expect(row).toBeVisible()

  const [ackResponse] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/notifications/${oldest.id}/ack`) && r.request().method() === 'POST',
    ),
    row.getByRole('button', { name: 'Квитировать' }).click(),
  ])
  const ackBody = (await ackResponse.json()) as { acked_by: string | null; acked_at: string | null }
  expect(ackBody.acked_by).toBe(process.env.E2E_LOGIN ?? 'dispatcher1')
  expect(ackBody.acked_at).toBeTruthy()

  await expect(page.locator(`[data-notification-id="${oldest.id}"]`)).toHaveCount(0)

  // Событие правда квитировано, а не просто спрятано на экране: смотрим в
  // acked=true, не в «нет в первых 200 acked=false» — та проверка была бы
  // истиной и без ack, самое старое событие туда всё равно не попадает.
  const ackedResp = await page.request.get('/api/notifications?acked=true&limit=1000')
  const ackedItems = (
    (await ackedResp.json()) as {
      items: (Notification & { acked_by: string | null })[]
    }
  ).items
  const ackedRow = ackedItems.find((n) => n.id === oldest.id)
  expect(ackedRow?.acked_by).toBe(process.env.E2E_LOGIN ?? 'dispatcher1')
})

// Регресс на подменённом API (находка 0d, 27.09.2026): «Показать ещё» считал
// следующий offset накопительно, o + PAGE_SIZE. Квитирование на первой
// странице убирает строку из выдачи без изменения offset, и следующая
// страница начиналась не там, где экран остановился, — ровно одна строка
// на стыке пропадала навсегда. Стенд не квитирует — весь список подставной.
test('US-04 сц. 5: «Показать ещё» не теряет строку после квитирования на первой странице', async ({
  page,
}) => {
  const N = 250
  let store = Array.from({ length: N }, (_, i) => ({
    id: 1000 + i,
    reported_at: new Date(Date.UTC(2026, 8, 20) - i * 60_000).toISOString(),
    object_name: `obj${i}`,
    smvu_key: `k${i}`,
    probability: 0.9,
    horizon_h: 24,
    as_of: null,
    acked_at: null,
    acked_by: null,
  }))

  await page.route(/\/api\/notifications(\?|\/)/, async (route) => {
    const u = new URL(route.request().url())
    const ackMatch = u.pathname.match(/\/notifications\/(\d+)\/ack$/)
    if (ackMatch && route.request().method() === 'POST') {
      const id = +ackMatch[1]
      store = store.filter((n) => n.id !== id)
      return route.fulfill({
        json: { id, acked_by: 'dispatcher1', acked_at: new Date().toISOString() },
      })
    }
    const limit = +(u.searchParams.get('limit') ?? 200)
    const offset = +(u.searchParams.get('offset') ?? 0)
    return route.fulfill({
      json: { total: store.length, items: store.slice(offset, offset + limit) },
    })
  })

  await page.goto('/orders')
  await page.getByRole('tab', { name: 'Неквитированные' }).click()
  await page.getByText(`показано 200 из ${N}`).waitFor()

  await page
    .locator('[data-notification-id="1000"]')
    .getByRole('button', { name: 'Квитировать' })
    .click()
  await page.getByText(`показано 199 из ${N - 1}`).waitFor()

  await page.getByRole('button', { name: 'Показать ещё' }).click()
  await expect(page.getByText(`показано ${N - 1} из ${N - 1}`)).toBeVisible()

  const shownIds = await page
    .locator('[data-notification-id]')
    .evaluateAll((els) => els.map((e) => +(e as HTMLElement).dataset.notificationId!))
  const missing = store.map((n) => n.id).filter((id) => !shownIds.includes(id))
  expect(missing).toEqual([])
})

// ── Полоса уведомлений AlertBar (MOS-53, план 5.6) ─────────────────────────
// Полоса берёт GET /api/notifications?acked=false и показывает запись с
// наибольшим id: у событий проигрывания СМВУ reported_at майский, и первым
// в ответе (reported_at DESC) стоит не самое новое событие.
interface BarItem {
  id: number
  object_name: string | null
  section_id: number | null
  probability: number
  current_probability?: number | null
  horizon_h: number
}

async function свежее(page: import('@playwright/test').Page): Promise<BarItem> {
  const r = await page.request.get('/api/notifications?acked=false&limit=1000')
  const { items } = (await r.json()) as { items: BarItem[] }
  return items.reduce((a, b) => (a.id > b.id ? a : b))
}

const полоса = (page: import('@playwright/test').Page) =>
  page.getByRole('status', { name: 'Уведомление о прогнозе' })

test('US-04 сц. 2: в уведомлении всё для первого решения', async ({ page }) => {
  const n = await свежее(page)
  await page.goto('/log')
  const bar = полоса(page)
  await expect(bar).toContainText(`${Math.round(n.probability * 100)} %`)
  await expect(bar).toContainText(`${n.horizon_h} ч`)
  await expect(bar).toContainText(n.object_name!)
})

test('US-04 сц. 3: из уведомления один переход к участку', async ({ page }) => {
  const n = await свежее(page)
  expect(n.section_id, 'GET /api/notifications отдаёт section_id').toBeTruthy()
  await page.goto('/map')
  await полоса(page).getByRole('link', { name: n.object_name! }).click()
  await expect(page).toHaveURL(new RegExp(`/objects/${n.section_id}$`))
})

// Подменённые API и поток: на стенде за 70 с не появилось ни одного нового
// уведомления (27.09.2026, max id 7542 до и после), ждать живое событие тест
// не может. Стартовая картина — GET /api/notifications?acked=false, новое —
// событие SSE из GET /api/alerts/stream (id события = id уведомления).
// Маршруты ждём до goto: без await первый запрос полосы успевал уйти на живой
// стенд, и сц. 1 и 6 падали в общем прогоне (27.09.2026), а поодиночке проходили.
async function подменить(page: import('@playwright/test').Page) {
  const state = { list: [] as object[], stream: [] as object[], ack: 0 }
  await page.route(/\/api\/notifications(\?|\/)/, (route) => {
    if (route.request().method() === 'POST') {
      state.ack++
      return route.fulfill({ json: { id: 0, acked_by: 'x', acked_at: 'x' } })
    }
    return route.fulfill({ json: { total: state.list.length, items: state.list } })
  })
  // Каждое подключение отдаёт накопленные события и закрывается — EventSource
  // переподключается сам через ~3 с с Last-Event-ID, как после обрыва nginx.
  await page.route('**/api/alerts/stream', (route) => {
    const body = state.stream
      .map((e) => `id: ${(e as { id: number }).id}\ndata: ${JSON.stringify(e)}\n\n`)
      .join('')
    state.stream = []
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: body || ': ping\n\n',
    })
  })
  return state
}

const запись = (id: number) => ({
  id,
  reported_at: '2026-09-27T10:00:00+00:00',
  object_name: `Коллектор 9, пикет ${id}`,
  smvu_key: `9:${id}`,
  section_id: 401,
  probability: 0.91,
  horizon_h: 24,
  as_of: null,
})

test('US-04 сц. 1: уведомление приходит само', async ({ page }) => {
  const state = await подменить(page)
  await page.goto('/dashboard')
  await expect(page.getByRole('heading', { name: 'Дашборд рисков' })).toBeVisible()
  await expect(полоса(page)).toHaveCount(0)

  state.stream = [запись(9001)]
  // Не перезагружаем страницу: событие доходит по переподключению потока.
  await expect(полоса(page)).toContainText('Коллектор 9, пикет 9001', { timeout: 15_000 })
})

test('US-04 сц. 6: «Принял» гасит полосу, а событие остаётся неквитированным', async ({ page }) => {
  const state = await подменить(page)
  state.list = [{ ...запись(9001), acked_at: null, acked_by: null }]
  await page.goto('/dashboard')
  await полоса(page).getByRole('button', { name: 'Принял' }).click()
  await expect(полоса(page)).toHaveCount(0)
  expect(state.ack, 'POST …/ack не уходил').toBe(0)

  // После перезагрузки то же событие полосу не зажигает, новое из потока — зажигает.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Дашборд рисков' })).toBeVisible()
  await expect(полоса(page)).toHaveCount(0)
  state.stream = [запись(9002)]
  await expect(полоса(page)).toContainText('пикет 9002', { timeout: 15_000 })
})

test('US-04 сц. 7: из уведомления — на схему', async ({ page }) => {
  const n = await свежее(page)
  await page.goto('/dashboard')
  await полоса(page).getByRole('link', { name: 'на схеме' }).click()
  await expect(page).toHaveURL(new RegExp(`/map\\?section=${n.section_id}$`))
  const метка = page.locator(`[data-section-id="${n.section_id}"][data-selected]`)
  await expect(метка).toBeVisible()
  await expect(page.getByText(`Выбран участок: ${n.object_name}`)).toBeVisible()
})

// Фильтр схемы мог спрятать участок — переход по адресу его сбрасывает (MOS-245).
test('US-04 сц. 7: переход на схему сбрасывает фильтр, который прятал участок', async ({
  page,
}) => {
  const n = await свежее(page)
  const risks = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    risk_class: string | null
  }[]
  const класс = risks.find((r) => r.section_id === n.section_id)?.risk_class
  await page.goto('/map')
  await page
    .getByLabel('Уровень риска')
    .selectOption({ label: класс === 'high' ? 'Низкий' : 'Высокий' })
  await полоса(page).getByRole('link', { name: 'на схеме' }).click()
  await expect(page.locator(`[data-section-id="${n.section_id}"][data-selected]`)).toBeVisible()
  await expect(page.getByLabel('Уровень риска')).toHaveValue('all')
})

test('US-04 сц. 7: из карточки участка — на схему', async ({ page }) => {
  const n = await свежее(page)
  await page.goto(`/objects/${n.section_id}`)
  await page.getByRole('main').getByRole('link', { name: 'на схеме' }).click()
  await expect(page).toHaveURL(new RegExp(`/map\\?section=${n.section_id}$`))
  await expect(page.locator(`[data-section-id="${n.section_id}"][data-selected]`)).toBeVisible()
})

// Сц. 4 на живых данных: участок с самым низким риском. На пути модели v3
// уведомление заводит открытое моделью предупреждение, а не порог вероятности
// (backend/app/domain/order_rules.py, заявки_по_предупреждениям), поэтому
// проверяем то, что видит диспетчер: участок есть в списке рисков, а уведомления
// по нему нет ни среди неквитированных, ни среди квитированных.
test('US-04 сц. 4: риск ниже порога не отвлекает', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as {
    section_id: number
    probability: number
    risk_class: string | null
  }[]
  const низкий = риски.reduce((a, b) => (a.probability < b.probability ? a : b))
  expect(низкий.risk_class, 'самый низкий риск — не «высокий»').not.toBe('high')

  const все: { section_id: number | null }[] = []
  for (const acked of ['false', 'true']) {
    const r = await page.request.get(`/api/notifications?acked=${acked}&limit=1000`)
    все.push(...((await r.json()) as { items: { section_id: number | null }[] }).items)
  }
  expect(
    все.filter((n) => n.section_id === низкий.section_id),
    `уведомлений по участку ${низкий.section_id} (p = ${низкий.probability}) нет`,
  ).toEqual([])

  await page.goto('/dashboard?view=sections')
  await expect(
    page.locator('main table tbody tr', { hasText: new RegExp(`·\\s*${низкий.section_id}\\b`) }),
    'прогноз виден в списке рисков',
  ).toHaveCount(1)
})

// MOS-247: плашка писала «91 %» — вероятность прогноза, поднявшего уведомление, —
// а карточка того же участка 0,8169, текущую. Теперь ответ несёт обе, и плашка
// называет текущую словом «сейчас», когда проценты разошлись.
test('US-04 сц. 8: плашка и карточка называют одну текущую вероятность', async ({ page }) => {
  const n = await свежее(page)
  expect(n.section_id, 'у уведомления есть участок').toBeTruthy()
  const карточка = (await (await page.request.get(`/api/objects/${n.section_id}`)).json()) as {
    current_risk: { probability: number } | null
  }
  const сейчас = карточка.current_risk?.probability
  expect(сейчас, 'у участка есть текущий прогноз').toBeDefined()
  expect(n.current_probability, 'GET /api/notifications отдаёт current_probability').toBeCloseTo(
    сейчас!,
    6,
  )

  await page.goto('/log')
  const bar = полоса(page)
  await expect(bar).toContainText(`${Math.round(n.probability * 100)} %`)
  if (Math.round(сейчас! * 100) !== Math.round(n.probability * 100))
    await expect(bar).toContainText(`сейчас ${Math.round(сейчас! * 100)} %`)
})
