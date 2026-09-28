// Экраны по датчикам — SL.5 (дашборд, MOS-254) и SL.6 (схема, MOS-255), эпик MOS-248.
// Ответы GET /api/sensor-risk и /summary — из мока helpers/sensor-mock.ts по контракту
// SL.4 (MOS-253). Всё остальное — стенд. E2E_SENSOR_MOCK=0 — и датчики со стенда:
// ожидания тесты берут из того, что отдал сервер (mockSensorRisk в живом режиме).
//
// Запуск с локальной сборкой: BASE_URL — сервер, который отдаёт dist/ и проксирует
// /api и /data на стенд. E2E_SHOTS=<каталог> — ещё и снимки экранов на 1440 и 390.
import { expect, test, type Page } from '@playwright/test'
import { слово, type Формы } from '../src/lib/plural'
import { mockSensorRisk, КАППА, МЮ } from './helpers/sensor-mock'

const ошибкиКонсоли = (page: Page) => {
  const ошибки: string[] = []
  page.on('console', (m) => m.type() === 'error' && ошибки.push(m.text()))
  return ошибки
}
const число = (s: string) => Number(s.replace(/\s/g, ''))
const плитка = (page: Page, имя: string) => page.locator('article', { hasText: имя })

test('SL.5: дашборд по датчикам — плитки, коллекторы, таблица постранично', async ({ page }) => {
  const ошибки = ошибкиКонсоли(page)
  const мок = await mockSensorRisk(page)
  const парк = мок.items(true)
  const high = парк.filter((s) => s.level === 'high')
  const коллекторов = new Set(high.map((s) => s.collector_id)).size

  await page.goto('/dashboard')
  await expect(page.getByRole('link', { name: 'По датчикам' })).toHaveAttribute(
    'aria-current',
    'page',
  )
  await expect(page.getByTestId('synthetic-toggle')).toBeChecked()
  await expect(page.getByTestId('synthetic-note')).toContainText('Демо: паспорта синтетические')

  // Плитка: число датчиков high и «на N коллекторах».
  const высокий = плитка(page, 'Высокий риск')
  await expect(высокий.locator('div').first()).toHaveText(String(high.length))
  await expect(высокий).toContainText(`на ${коллекторов} коллектор`)

  // «Где риск сосредоточен» — коллекторы по числу датчиков high, первый — самый нагруженный.
  const поКоллекторам = new Map<string, number>()
  for (const s of high)
    поКоллекторам.set(s.collector_name, (поКоллекторам.get(s.collector_name) ?? 0) + 1)
  const первый = [...поКоллекторам.entries()].sort((a, b) => b[1] - a[1])[0]
  const очаги = page.getByTestId('sensor-hotspots').locator('li')
  await expect(очаги.first()).toContainText(первый[0])
  await expect(очаги.first()).toContainText(String(первый[1]))

  // Таблица: 50 строк, самый рискованный датчик первым, главная причина — самая весомая.
  const строки = page.getByTestId('sensor-table').locator('tbody tr')
  await expect(строки).toHaveCount(50)
  await expect(строки.first()).toHaveAttribute('data-channel-id', String(парк[0].channel_id))
  const ячейки = строки.first().locator('td')
  await expect(ячейки.nth(0).locator('[data-level]')).toHaveAttribute('data-level', парк[0].level)
  await expect(ячейки.nth(1)).toHaveText(парк[0].score.toFixed(2))
  await expect(ячейки.nth(2)).toContainText(парк[0].name)
  await expect(ячейки.nth(3)).toHaveText(парк[0].sensor_kind)
  await expect(ячейки.nth(4)).toHaveText(`${парк[0].collector_name} · ПК${парк[0].picket}`)
  const главная = парк[0].reasons
    .filter((r) => r.kind !== 'plan')
    .reduce((a, b) => (b.weight > a.weight ? b : a))
  await expect(ячейки.nth(5)).toContainText(главная.text)

  const страница = page.getByTestId('sensor-page')
  expect(число((await страница.innerText()).split('из')[1])).toBe(парк.length)
  await page.getByRole('button', { name: 'следующие →' }).click()
  await expect(страница).toHaveText(/^51–100 из/)
  await expect(строки.first()).toHaveAttribute('data-channel-id', String(парк[50].channel_id))

  // Отбор по уровню — с первой страницы и в адресе (MOS-262).
  await page.getByLabel('Уровень').selectOption('high')
  await expect(page).toHaveURL(/\/dashboard\?level=high$/)
  await expect(страница).toHaveText(new RegExp(`из ${high.length}$`))
  expect(await строки.locator('[data-level="high"]').count()).toBe(Math.min(50, high.length))

  expect(ошибки).toEqual([])
})

test('SL.5: переключатель синтетики — в адресе, запросы и плашка следуют за ним', async ({
  page,
}) => {
  const ошибки = ошибкиКонсоли(page)
  const мок = await mockSensorRisk(page)
  await page.goto('/dashboard')
  await expect(page.getByTestId('sensor-table').locator('tbody tr')).toHaveCount(50)

  await page.getByTestId('synthetic-toggle').uncheck()
  await expect(page).toHaveURL(/\/dashboard\?synthetic=0$/)
  await expect(page.getByTestId('synthetic-note')).toHaveCount(0)
  const безПаспорта = мок.items(false).filter((s) => s.level === 'high').length
  await expect(плитка(page, 'Высокий риск').locator('div').first()).toHaveText(String(безПаспорта))
  expect(мок.urls.some((u) => u.startsWith('/api/sensor-risk/summary?synthetic=0'))).toBe(true)
  expect(мок.urls.some((u) => u.startsWith('/api/sensor-risk?synthetic=0&limit=50'))).toBe(true)

  // Адрес с ?synthetic=0 открывает экран уже выключенным.
  await page.reload()
  await expect(page.getByTestId('synthetic-toggle')).not.toBeChecked()

  // Клик по строке — на схему с датчиком, переключатель едет с ним.
  const строка = page.getByTestId('sensor-table').locator('tbody tr').first()
  const id = await строка.getAttribute('data-channel-id')
  await строка.click()
  await expect(page).toHaveURL(new RegExp(`/map\\?channel=${id}&synthetic=0$`))
  await expect(page.getByTestId('synthetic-toggle')).not.toBeChecked()
  await expect(page.locator(`li[data-channel-id="${id}"] > button`)).toHaveAttribute(
    'aria-expanded',
    'true',
  )

  expect(ошибки).toEqual([])
})

test('SL.5: вид «по участкам» остался вторым режимом', async ({ page }) => {
  await mockSensorRisk(page)
  await page.goto('/dashboard')
  await page.getByRole('link', { name: 'По участкам' }).click()
  await expect(page).toHaveURL(/\/dashboard\?view=sections$/)
  await expect(page.getByRole('heading', { name: /Все участки по риску/ })).toBeVisible({
    timeout: 30_000,
  })
  await expect(плитка(page, 'Участков в расчёте')).toBeVisible()
  await expect(page.getByTestId('sensor-table')).toHaveCount(0)
})

// 267052 «ФАО2 щит. ПК632» — high и с паспортом, и без: так на стенде и в моке.
// Число high на пикете тест берёт из ответа, а не зашивает: на моке 5 и 2, на стенде
// 28.09.2026 — 3 и 2.
test('SL.6: /map?channel= выбирает коллектор и пикет, раскрывает датчик; high на ПК632', async ({
  page,
}) => {
  const ошибки = ошибкиКонсоли(page)
  const мок = await mockSensorRisk(page)
  const highНаПК632 = (synthetic: boolean) =>
    мок
      .items(synthetic)
      .filter((s) => s.collector_id === КАППА && s.picket === 632 && s.level === 'high').length
  const [сПаспортом, безПаспорта] = [highНаПК632(true), highНаПК632(false)]
  expect(безПаспорта).toBeGreaterThanOrEqual(1)
  expect(безПаспорта).toBeLessThanOrEqual(сПаспортом)
  await page.goto('/map?channel=267052')
  const демо = page.getByTestId('sensor-demo')
  await expect(page.locator('main select').first()).toHaveValue(String(КАППА))
  await expect(демо.getByTestId('sensor-picket')).toContainText('ПК632')
  await expect(демо.locator('g[data-picket="632"][data-selected]')).toHaveCount(1)
  const датчик = демо.locator('li[data-channel-id="267052"]')
  await expect(датчик.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expect(датчик.locator('[data-level]').first()).toHaveAttribute('data-level', 'high')
  const пикет = демо.locator('li[data-channel-id]')
  await expect(пикет.locator('button [data-level="high"]')).toHaveCount(сПаспортом)
  await expect(демо.getByRole('note')).toContainText('Демо: паспорта синтетические')

  // Без паспорта high на пикете не больше, чем с ним; датчик остаётся раскрытым.
  await демо.getByTestId('synthetic-toggle').uncheck()
  await expect(page).toHaveURL(/\/map\?channel=267052&synthetic=0$/)
  await expect(пикет.locator('button [data-level="high"]')).toHaveCount(безПаспорта)
  await expect(датчик.locator('button [data-level]')).toHaveAttribute('data-level', 'high')
  await expect(датчик.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expect(датчик).toContainText('Паспорта оборудования нет.')
  await expect(демо.getByRole('note')).toHaveCount(0)

  expect(ошибки).toEqual([])
})

test('SL.6: самый плотный коллектор — «объект Мю», 1 487 каналов, по полосе на линию', async ({
  page,
}) => {
  const ошибки = ошибкиКонсоли(page)
  const мок = await mockSensorRisk(page)
  const мю = мок.items(true).filter((s) => s.collector_id === МЮ)
  // На моке 1 487, как на стенде 28.09.2026; на оси — только датчики с пикетом.
  expect(мю.length).toBeGreaterThan(1000)
  const наОси = мю.filter((s) => s.picket != null).length

  const начало = Date.now()
  await page.goto(`/map?collector=${МЮ}`)
  const демо = page.getByTestId('sensor-demo')
  await expect(демо).toContainText(`${мю.length} датчиков`)
  console.log(`Мю: блок датчиков готов за ${Date.now() - начало} мс`)
  await expect(page.locator('main select').first()).toHaveValue(String(МЮ))

  // Линии, как у оси: 914 и 915, у каждой своя полоса и свои кнопки масштаба.
  await expect(демо.locator('[data-line="914"]')).toHaveCount(1)
  await expect(демо.locator('[data-line="915"]')).toHaveCount(1)
  for (const линия of ['914', '915']) {
    const полоса = демо.locator(`[data-line="${линия}"]`)
    await expect(полоса.getByRole('group')).toBeVisible()
    // Стопка сжата: чертёж не выше 12 + 140 + 34 px, сколько бы датчиков ни стояло на пикете.
    const высота = await полоса.locator('svg[role="img"]').evaluate((e) => e.clientHeight)
    expect(высота).toBeLessThanOrEqual(188)
  }
  // Каждый датчик с пикетом учтён: рискованный нарисован значком (не больше 8 на пикет,
  // кольцо ППР — отдельный круг без заливки) или вошёл в число «+N» над стопкой, а датчик
  // в норме — в число data-normal стопки (MOS-265: значком он больше не рисуется).
  const значков = await демо
    .locator('g[data-picket] > :is(polygon, circle):not([fill="none"])')
    .count()
  const скрыто = await демо
    .locator('g[data-picket] > text[data-hidden]')
    .evaluateAll((es) => es.reduce((n, e) => n + Number(e.getAttribute('data-hidden')), 0))
  const вНорме = await демо
    .locator('g[data-picket]')
    .evaluateAll((es) => es.reduce((n, e) => n + Number(e.getAttribute('data-normal')), 0))
  await expect(демо.locator('g[data-picket] [data-level="normal"]')).toHaveCount(0)
  expect(значков + скрыто + вНорме).toBe(наОси)
  for (const g of await демо.locator('g[data-picket]').all())
    expect(
      await g.locator(':scope > :is(polygon, circle):not([fill="none"])').count(),
    ).toBeLessThanOrEqual(8)

  // Датчик с другой линии по адресу: выбрана его линия и пикет, полоса приближена.
  const участки = (await (await page.request.get('/data/sections.json')).json()) as {
    section_id: number
    smvu_key: string
  }[]
  const линии915 = new Set(
    участки.filter((у) => у.smvu_key.startsWith('915:')).map((у) => у.section_id),
  )
  const на915 = мю.find(
    (s) => s.picket != null && s.section_id != null && линии915.has(s.section_id),
  )!
  await page.goto(`/map?channel=${на915.channel_id}`)
  await expect(демо.getByTestId('sensor-picket')).toContainText(`ПК${на915.picket}`)
  await expect(демо.locator(`li[data-channel-id="${на915.channel_id}"] > button`)).toHaveAttribute(
    'aria-expanded',
    'true',
  )
  await expect(демо.locator('[data-line="915"] g[data-selected]')).toHaveCount(1)
  // Окно полосы 915 сужено до десятой части линии — кнопка «вся линия» ожила.
  await expect(
    демо.locator('[data-line="915"]').getByRole('button', { name: 'вся линия' }),
  ).toBeEnabled()

  expect(ошибки).toEqual([])
})

// Правый край самого широкого элемента внутри root, кроме блоков с собственной прокруткой.
// На схеме root — блок датчиков: кнопка «вся линия» оси участков (AxisLine.tsx)
// на 390 px вылезает на 3 px, и это тоже не SL.6.
const правыйКрай = (page: Page, root = 'main') =>
  page.evaluate(
    (root) =>
      Math.max(
        ...[...document.querySelectorAll(`${root} *`)]
          .filter((e) => !e.closest('.overflow-x-auto'))
          .map((e) => e.getBoundingClientRect().right),
      ),
    root,
  )

const КАТАЛОГ = process.env.E2E_SHOTS
for (const [ширина, высота] of [
  [1440, 900],
  [390, 844],
] as const) {
  test(`снимки экранов на ${ширина} px`, async ({ page }) => {
    test.skip(!КАТАЛОГ, 'E2E_SHOTS не задан')
    await page.setViewportSize({ width: ширина, height: высота })
    await mockSensorRisk(page)
    await page.goto('/dashboard')
    await expect(page.getByTestId('sensor-table').locator('tbody tr')).toHaveCount(50)
    // Содержимое экрана не шире окна: широкая таблица листается в своём блоке.
    // Мерим main: шапку (Nav.tsx) на 390 px проверяет e2e/layout-390.spec.ts.
    expect(await правыйКрай(page)).toBeLessThanOrEqual(ширина)
    await page.screenshot({ path: `${КАТАЛОГ}/dashboard-${ширина}.png`, fullPage: true })
    await page.goto('/map?channel=267052')
    await expect(page.locator('li[data-channel-id="267052"] > button')).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(await правыйКрай(page, '[data-testid=sensor-demo]')).toBeLessThanOrEqual(ширина)
    await page.screenshot({ path: `${КАТАЛОГ}/map-kappa-${ширина}.png`, fullPage: true })
    await page.goto(`/map?collector=${МЮ}`)
    await expect(page.getByTestId('sensor-demo')).toContainText('датчиков')
    await page.getByTestId('sensor-demo').scrollIntoViewIfNeeded()
    await page.getByTestId('sensor-demo').screenshot({ path: `${КАТАЛОГ}/map-mu-${ширина}.png` })
  })
}

// MOS-262 п. 1: каждое «число + слово» в подписях блока датчиков и оси участков —
// в своей форме. На Каппе и Мю числа разные: 1 487 датчиков, линии, пикеты по 1–6.
const ФОРМЫ: [RegExp, Формы][] = [
  [/^датчик(а|ов)?$/, ['датчик', 'датчика', 'датчиков']],
  [/^участ(ок|ка|ков)$/, ['участок', 'участка', 'участков']],
  [/^лини(я|и|й)$/, ['линия', 'линии', 'линий']],
]
test('MOS-262: склонение — нет «1 датчиков», «1 участков», «4 высокий риск»', async ({ page }) => {
  await mockSensorRisk(page)
  for (const адрес of [`/map?collector=${КАППА}`, `/map?channel=267052`, `/map?collector=${МЮ}`]) {
    await page.goto(адрес)
    const демо = page.getByTestId('sensor-demo')
    // Мю — 1 487 датчиков: блок на схеме появляется не сразу.
    await expect(демо, адрес).toContainText('расчёт на', { timeout: 60_000 })
    // Подписи на экране и всплывающие <title> над стопками пикетов.
    const тексты = [
      await page.locator('main').innerText(),
      ...(await демо.locator('svg desc').allTextContents()),
    ]
    let проверено = 0
    for (const текст of тексты)
      for (const [, n, w] of текст.matchAll(/(?<![\d–.,])(\d+) ([а-яё]+)/g)) {
        // «ПК632 из 1 участка» — после «из» родительный падеж, его ведёт изУчастков.
        const формы = ФОРМЫ.find(([re]) => re.test(w))?.[1]
        if (!формы || /из $/.test(текст.slice(0, текст.indexOf(`${n} ${w}`)))) continue
        expect(w, `${адрес}: «${n} ${w}»`).toBe(слово(Number(n), формы))
        проверено += 1
      }
    expect(проверено, `${адрес}: подписи с числом найдены`).toBeGreaterThan(0)
    await expect(демо).not.toContainText(/\d высокий риск/)
  }
})

test('MOS-262: ?level= переживает перезагрузку, «Назад» возвращает прежний фильтр', async ({
  page,
}) => {
  const мок = await mockSensorRisk(page)
  const парк = мок.items(true)
  const страница = page.getByTestId('sensor-page')
  const уровень = page.getByLabel('Уровень')

  await page.goto('/dashboard?level=high')
  await expect(уровень).toHaveValue('high')
  const high = парк.filter((s) => s.level === 'high').length
  await expect(страница).toHaveText(new RegExp(`из ${high}$`))

  await page.reload()
  await expect(page).toHaveURL(/\/dashboard\?level=high$/)
  await expect(уровень).toHaveValue('high')
  // Последний запрос таблицы, а не сводки: сводку зовёт и шапка (период данных).
  expect(мок.urls.filter((u) => !u.includes('/summary')).at(-1)).toContain('level=high')

  await уровень.selectOption('watch')
  await expect(page).toHaveURL(/\/dashboard\?level=watch$/)
  const watch = парк.filter((s) => s.level === 'watch').length
  await expect(страница).toHaveText(new RegExp(`из ${watch.toLocaleString('ru-RU')}$`))

  await page.goBack()
  await expect(page).toHaveURL(/\/dashboard\?level=high$/)
  await expect(уровень).toHaveValue('high')
  await expect(страница).toHaveText(new RegExp(`из ${high}$`))

  // «все» убирает параметр, синтетика рядом с ним не теряется.
  await page.goto('/dashboard?synthetic=0&level=high')
  await уровень.selectOption('')
  await expect(page).toHaveURL(/\/dashboard\?synthetic=0$/)
})

test('MOS-262: на 390 px первые колонки таблицы — «Уровень» и «Балл», видны без прокрутки', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockSensorRisk(page)
  await page.goto('/dashboard')
  const таблица = page.getByTestId('sensor-table')
  await expect(таблица.locator('tbody tr')).toHaveCount(50)
  const шапка = таблица.locator('thead th')
  await expect(шапка.nth(0)).toHaveText('Уровень')
  await expect(шапка.nth(1)).toHaveText('Балл')
  const балл = await шапка.nth(1).boundingBox()
  expect(балл!.x + балл!.width).toBeLessThanOrEqual(390)
})
