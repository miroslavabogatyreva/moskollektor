// US-05. Найти участок на схеме коллектора — docs/user-stories.md, приёмка М-05, план 5.30.
// Названия test() — дословно названия сценариев истории.
import { expect, test, type Page } from '@playwright/test'

// Порог назван до замера: две картинки различимы, если силуэты расходятся хотя бы
// на 10 % площади. Ниже лежат пары, которые глазом не различаются: восьмиугольник
// и круг в MOS-172 — 2 px из 144 (1,4 %), пустой квадрат и пустой круг при 10 px —
// 8 из 144 (5,6 %, модель проверяющей 4d).
const ПОРОГ = 0.1
const СОСТОЯНИЯ = ['высокий риск', 'низкий риск', 'класса нет']

// Экран как ч/б распечатка: тёмная заливка печатается чёрным, светлая остаётся
// бумагой, обводки и линии печатаются всегда. Серым сравнивать нельзя: одинаковые
// квадраты high (яркость 71) и normal (234) прошли бы как «разные». Красить в чёрный
// всё подряд тоже нельзя: пустой круг станет диском, а палитра (разд. 8.4) считает
// «круг» и «пустой круг» разными формами. Линию оси и рамку не прячем: значок обязан
// отличаться и от того, на чём стоит (находка 4d — горизонтальная черта ложилась ровно
// на ось). Прячем только подписи. Зовём перед каждым снимком: смена коллектора и зум
// рисуют элементы заново.
async function напечатать(page: Page) {
  await page.evaluate(() => {
    const тёмный = (цвет: string) => {
      const [r, g, b] = цвет.match(/[\d.]+/g)!.map(Number)
      return 0.299 * r + 0.587 * g + 0.114 * b < 128
    }
    for (const эл of document.querySelectorAll<SVGElement>('main svg *')) {
      const ст = getComputedStyle(эл)
      if (эл.tagName === 'text') эл.style.display = 'none'
      if (ст.fill !== 'none') эл.style.fill = тёмный(ст.fill) ? '#000' : '#fff'
      if (ст.stroke !== 'none') эл.style.stroke = '#000'
    }
    for (const эл of document.querySelectorAll<HTMLElement>('main span[aria-hidden="true"]')) {
      эл.style.background = тёмный(getComputedStyle(эл).backgroundColor) ? '#000' : '#fff'
      эл.style.borderColor = '#000'
    }
  })
}

type Рамка = { x: number; y: number; width: number; height: number }

// Снимок с полем в 1 px: координаты дробные (x = 136,75), и без поля край значка
// отрезается по-разному у разных значков.
async function снимок(page: Page, r: Рамка) {
  await напечатать(page)
  const x = Math.floor(r.x) - 1
  const y = Math.floor(r.y) - 1
  const clip = {
    x,
    y,
    width: Math.ceil(r.x + r.width) + 1 - x,
    height: Math.ceil(r.y + r.height) + 1 - y,
  }
  return (await page.screenshot({ clip })).toString('base64')
}

// Считаем краску, которая выступает за краску другой картинки дальше 1 px. Кайму в 1 px
// не считаем: там сглаживание и дробная координата, а не форма. Без допуска одинаковые
// квадраты старой оси расходились на 14 px из 110 (сдвиг на пиксель), а горизонтальная
// черта на линии оси — на 7 из 66, и обе пары прошли бы порог. Два режима.
// «выровнять» — значки разных участков: силуэты сдвигаем к левому верхнему углу краски
// и считаем в обе стороны. Без него — значок (a) и то же место без значка (b): считаем
// только краску значка.
async function различие(page: Page, a: string, b: string, выровнять: boolean) {
  return page.evaluate(
    async ([a, b, выровнять]) => {
      const силуэт = async (b64: string) => {
        const img = new Image()
        img.src = `data:image/png;base64,${b64}`
        await img.decode()
        const c = document.createElement('canvas')
        c.width = img.width
        c.height = img.height
        const ctx = c.getContext('2d')!
        ctx.drawImage(img, 0, 0)
        const d = ctx.getImageData(0, 0, c.width, c.height).data
        const точки: [number, number][] = []
        for (let i = 0; i < d.length; i += 4)
          if (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] < 128)
            точки.push([(i / 4) % c.width, Math.floor(i / 4 / c.width)])
        const x0 = выровнять ? Math.min(...точки.map((т) => т[0])) : 0
        const y0 = выровнять ? Math.min(...точки.map((т) => т[1])) : 0
        return {
          н: new Set(точки.map(([x, y]) => `${x - x0},${y - y0}`)),
          площадь: c.width * c.height,
        }
      }
      const [sa, sb] = [await силуэт(a as string), await силуэт(b as string)]
      // Краска одного силуэта дальше 1 px от краски другого.
      const торчит = (из: Set<string>, от: Set<string>) =>
        [...из].filter((т) => {
          const [x, y] = т.split(',').map(Number)
          for (let dx = -1; dx <= 1; dx++)
            for (let dy = -1; dy <= 1; dy++) if (от.has(`${x + dx},${y + dy}`)) return false
          return true
        }).length
      if (!выровнять) return { разных: торчит(sa.н, sb.н), площадь: sa.площадь }
      return {
        разных: торчит(sa.н, sb.н) + торчит(sb.н, sa.н),
        площадь: Math.max(sa.площадь, sb.площадь),
      }
    },
    [a, b, выровнять] as const,
  )
}

// Значок участка без соседа ближе 8 px (густой режим, метки перекрываются) или чип
// у рамки с номером (обычная плотность, соседи не ближе 24 ед.). Помечает значок
// атрибутом, чтобы потом снять то же место без него.
async function одинокийЗначок(page: Page, слово: string, чип: boolean): Promise<Рамка | null> {
  return page.evaluate(
    ([слово, чип]) => {
      document.querySelector('[data-e2e]')?.removeAttribute('data-e2e')
      const метки = [...document.querySelectorAll('svg[role="img"] :has(> title)')]
      const рамки = метки.map((м) => м.getBoundingClientRect())
      for (let i = 0; i < метки.length; i++) {
        if (!метки[i].querySelector('title')!.textContent!.endsWith(слово as string)) continue
        if (чип !== рамки[i].width > 12) continue
        const ц = рамки[i]
        const сосед = рамки.some((р, j) => j !== i && Math.abs(р.x - ц.x) < (чип ? 20 : 8))
        if (сосед) continue
        // До MOS-50 метка густого режима — сам <rect> с <title> внутри, отдельной
        // фигуры у неё нет; без этой ветки тест мерил бы пустой <title>.
        const м = метки[i]
        const значок = м.children.length > 1 ? м.lastElementChild! : м
        значок.setAttribute('data-e2e', '')
        const r = значок.getBoundingClientRect()
        return { x: r.x, y: r.y, width: r.width, height: r.height }
      }
      return null
    },
    [слово, чип] as const,
  )
}

// Снимок значка и того же места без него. Метки густой линии стоят через равные
// пикеты: 303 метки линии 847 — через 3,8 px; два шага зума разводят их до 15 px
// в густом режиме, четыре — переводят линию в обычную плотность с чипами.
async function значокИФон(page: Page, слово: string, чип: boolean) {
  const коллекторы = await page
    .locator('select')
    .first()
    .locator('option')
    .evaluateAll((оп) => оп.map((о) => (о as HTMLOptionElement).value))
  for (const к of коллекторы) {
    await page.locator('select').first().selectOption(к)
    const есть = await page.evaluate(
      (слово) =>
        [...document.querySelectorAll('svg[role="img"] title')].some((т) =>
          т.textContent!.endsWith(слово),
        ),
      слово,
    )
    if (!есть) continue
    for (let зум = 0; зум < 6; зум++) {
      const р = await одинокийЗначок(page, слово, чип)
      if (р) {
        const знак = await снимок(page, р)
        const площадь = Math.round(р.width * р.height)
        const скрыть = await page.addStyleTag({ content: '[data-e2e] { visibility: hidden }' })
        const фон = await снимок(page, р)
        await скрыть.evaluate((тег) => (тег as Element).remove())
        return { знак, фон, площадь }
      }
      await page.getByRole('button', { name: '+ приблизить' }).first().click()
      await page.waitForTimeout(300)
    }
  }
  throw new Error(`на оси нет одинокого значка «${слово}», ${чип ? 'чип' : 'густой режим'}`)
}

// Пары соседних рамок с номером пикета, чьи прямоугольники на экране пересекаются.
// По каждой линии отдельно: у линий свои оси, и рамки разных линий не соседи.
async function пересечения(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const найдено: string[] = []
    for (const svg of document.querySelectorAll('svg[role="img"]')) {
      const линия = (svg.getAttribute('aria-label') ?? '').split(',')[0]
      const рамки = [...svg.querySelectorAll('rect[width="20"]')]
        .map((р) => р.getBoundingClientRect())
        .sort((а, б) => а.left - б.left)
      for (let i = 1; i < рамки.length; i++)
        if (рамки[i].left < рамки[i - 1].right)
          найдено.push(`${линия} x=${Math.round(рамки[i].left)}`)
    }
    return найдено
  })
}

// MOS-215: правило плотности считало число меток, а не расстояние, и линия 798
// объекта Зита (21 метка, ПК55/56/57 через 13 ед.) рисовалась рамками внахлёст.
test('US-05 сц. 2: масштаб', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/map')
  await expect(page.locator('svg[role="img"]').first()).toBeVisible()
  const коллекторы = await page
    .locator('select')
    .first()
    .locator('option')
    .evaluateAll((оп) => оп.map((о) => (о as HTMLOptionElement).value))
  const найдено: string[] = []
  for (const к of коллекторы) {
    await page.locator('select').first().selectOption(к)
    найдено.push(...(await пересечения(page)).map((п) => `${к}: ${п}`))
  }
  expect(найдено, 'рамки внахлёст на полной оси').toEqual([])

  // Приближаем линию 798 объекта Зита до предела: кнопка гаснет, рамки не слипаются.
  const зита = await page
    .locator('select')
    .first()
    .locator('option', { hasText: /^объект Зита/ })
    .getAttribute('value')
  await page.locator('select').first().selectOption(зита!)
  // Родитель svg — блок одной линии: в нём её кнопки зума и больше ничьи.
  const строка = page.locator('svg[aria-label^="Линия 798"]').locator('..')
  const плюс = строка.getByRole('button', { name: '+ приблизить' })
  for (let шаг = 0; шаг < 12 && (await плюс.isEnabled()); шаг++) {
    await плюс.click()
    await page.waitForTimeout(200)
  }
  await expect(плюс).toBeDisabled()
  // Без рамок в окне «ноль пересечений» ничего не доказывает.
  expect(
    await строка.locator('rect[width="20"]').count(),
    'на пределе зума нет ни одной рамки',
  ).toBeGreaterThan(0)
  expect(await пересечения(page), 'рамки внахлёст на пределе зума').toEqual([])
})

test('US-05 сц. 5: риск различим без цвета', async ({ page }) => {
  test.setTimeout(120_000)
  // «Класса нет» стенд не отдаёт: /api/risks возвращает класс всем 3 173 участкам.
  // Чтобы оно появилось на оси, выкидываем из ответа стенда каждый третий участок.
  await page.route('**/api/risks', async (route) => {
    const ответ = await route.fetch()
    const строки = (await ответ.json()) as { section_id: number }[]
    await route.fulfill({ response: ответ, json: строки.filter((с) => с.section_id % 3 !== 0) })
  })
  await Promise.all([page.waitForResponse('**/api/risks'), page.goto('/map')])
  await expect(page.locator('svg[role="img"]').first()).toBeVisible()

  const легенда: string[] = []
  for (const слово of СОСТОЯНИЯ) {
    const значок = page
      .locator('span', { has: page.locator('[aria-hidden="true"]'), hasText: слово })
      .last()
      .locator('[aria-hidden="true"]')
    легенда.push(await снимок(page, (await значок.boundingBox())!))
  }
  // Густой режим, 6 ед. (так нарисованы 3 035 участков из 3 173), и чип, 7 ед.
  const густой = []
  const чипы = []
  for (const слово of СОСТОЯНИЯ) густой.push(await значокИФон(page, слово, false))
  for (const слово of СОСТОЯНИЯ) чипы.push(await значокИФон(page, слово, true))

  const пары: [string, string, string, boolean, number?][] = []
  for (const [a, b] of [
    [0, 1],
    [0, 2],
    [1, 2],
  ]) {
    const имя = `${СОСТОЯНИЯ[a]} / ${СОСТОЯНИЯ[b]}`
    пары.push([`легенда: ${имя}`, легенда[a], легенда[b], true])
    пары.push([`6 ед.: ${имя}`, густой[a].знак, густой[b].знак, true])
    пары.push([`7 ед.: ${имя}`, чипы[a].знак, чипы[b].знак, true])
  }
  for (let i = 0; i < 3; i++) {
    const г = густой[i]
    const ч = чипы[i]
    пары.push([`6 ед.: ${СОСТОЯНИЯ[i]} / ось без значка`, г.знак, г.фон, false, г.площадь])
    пары.push([`7 ед.: ${СОСТОЯНИЯ[i]} / рамка без чипа`, ч.знак, ч.фон, false, ч.площадь])
  }
  // У пары «значок / фон» доля считается от площади самого значка, а не кадра.
  for (const [имя, a, b, выровнять, площадьЗначка] of пары) {
    const р = await различие(page, a, b, выровнять)
    const разных = р.разных
    const площадь = площадьЗначка ?? р.площадь
    expect(площадь, `${имя}: значок не найден, мерить нечего`).toBeGreaterThan(0)
    console.log(`${имя}: ${разных} px из ${площадь} (${Math.round((100 * разных) / площадь)} %)`)
    expect.soft(разных, имя).toBeGreaterThanOrEqual(площадь * ПОРОГ)
  }
})

// Сценарии 6 и 7 — дерево объектов диспетчера слева от оси (план 5.9, MOS-101).
// Узел «ДП объект Бета» (object_id 111) выбран нарочно: в нём 23 участка, и один из
// них — 1490 «798:0» — единственный в парке на двух коллекторах; на оси он стоит
// под Зитой (большинство каналов, УЧАСТКИ_КОЛЛЕКТОРА), а в узел Беты входит тремя
// каналами. 23 — замер SQL на стенде 27.09.2026 (справочник каналов окончателен).
interface ДеревоУзел {
  object_id: number
  name: string
  channels: number
  section_ids: number[]
}
interface ДеревоКоллектор {
  object_id: number
  name: string
  nodes: ДеревоУзел[]
}
interface Участок {
  section_id: number
  collector: number
  kinds?: string[]
}

test('US-05 сц. 6: узел дерева сужает схему', async ({ page }) => {
  const дерево = (await (await page.request.get('/api/objects/tree')).json()) as ДеревоКоллектор[]
  expect(дерево).toHaveLength(16)
  const бета = дерево.find((к) => к.name === 'объект Бета')!
  const узел = бета.nodes.find((у) => у.object_id === 111)!
  expect(узел.name).toBe('ДП объект Бета')
  expect(узел.section_ids).toHaveLength(23)
  expect(узел.section_ids).toContain(1490)

  const участки = (await (await page.request.get('/data/sections.json')).json()) as Участок[]
  const наОсиБеты = участки.filter((у) => у.collector === бета.object_id)
  const узлаНаОси = наОсиБеты.filter((у) => узел.section_ids.includes(у.section_id))
  expect(узлаНаОси).toHaveLength(22) // 23 минус 1490, который на оси Зиты

  await page.goto('/map')
  const tree = page.getByRole('navigation', { name: 'Дерево объектов' })
  await tree.getByRole('button', { name: 'объект Бета', exact: true }).click()
  const меток = page.locator('main svg[role="img"] g > title')
  await expect(меток).toHaveCount(наОсиБеты.length)

  const кнопкаУзла = tree.getByRole('button', { name: /^ДП объект Бета · 81 кан\./ })
  await кнопкаУзла.click()
  await expect(кнопкаУзла).toHaveAttribute('aria-pressed', 'true')
  await expect(меток).toHaveCount(22)
  await expect(page.getByText(`22 из ${наОсиБеты.length} участков`)).toBeVisible()

  // Участок узла на оси другого коллектора — назван, со ссылкой на коллектор.
  const чужой = page.getByTestId('off-axis')
  await expect(чужой).toContainText('1490')
  await expect(чужой).toContainText('объект Зита')

  // Фильтр типа складывается с узлом, а не сбрасывает его.
  const охраняемых = узлаНаОси.filter((у) => (у.kinds ?? []).includes('guardObject')).length
  await page.getByLabel('Тип объекта').selectOption({ label: 'Охраняемый объект' })
  await expect(меток).toHaveCount(охраняемых)
  await expect(кнопкаУзла).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('Тип объекта').selectOption({ label: 'Все' })

  // Повторный клик снимает отбор — снова все метки коллектора.
  await кнопкаУзла.click()
  await expect(кнопкаУзла).toHaveAttribute('aria-pressed', 'false')
  await expect(меток).toHaveCount(наОсиБеты.length)

  // Переход по ссылке на ось чужого коллектора.
  await кнопкаУзла.click()
  await чужой.getByRole('button', { name: 'объект Зита' }).click()
  await expect(tree.getByRole('button', { name: 'объект Зита', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
})

test('US-05 сц. 7: карточка называет объект диспетчера', async ({ page }) => {
  await page.goto('/objects/1490')
  await expect(page.getByTestId('dispatcher-object')).toHaveText([
    'Объект диспетчера: ДП объект Бета → объект Бета',
    'Объект диспетчера: объект Фита → объект Зита',
    'Объект диспетчера: ДП объект Зита → объект Зита',
  ])
})

interface Риск {
  section_id: number
  probability: number
  risk_class: 'high' | 'normal' | null
}

const коллекторы = (page: Page) => page.locator('select').first()
// Значок уровня у метки: закрашенный треугольник или пустой круг. Кольцо выбранного
// участка — тоже circle, но с fill="none", его не берём.
const ЗНАЧОК = 'polygon, circle:not([fill="none"])'

test('US-05 сц. 1: риск виден на схеме', async ({ page }) => {
  test.setTimeout(120_000)
  const риски = (await (await page.request.get('/api/risks')).json()) as Риск[]
  const класс = new Map(риски.map((r) => [r.section_id, r.risk_class]))

  // Цвет уровня на дашборде — полоска строки.
  await page.goto('/dashboard?view=sections')
  const цвет: Record<string, string> = {}
  for (const [cls, слово] of [
    ['high', 'высокий риск'],
    ['normal', 'низкий риск'],
  ]) {
    const строка = page.locator('main table tbody tr').filter({ hasText: слово }).first()
    цвет[cls] = await строка.evaluate((e) => getComputedStyle(e).borderLeftColor)
  }

  // Метки рисуются до ответа /api/risks — тогда у всех значок «класса нет».
  const рискиЭкрана = page.waitForResponse((r) => r.url().endsWith('/api/risks'))
  await page.goto('/map')
  await рискиЭкрана
  await expect(page.locator('svg[role="img"]').first()).toBeVisible()
  const значения = await коллекторы(page)
    .locator('option')
    .evaluateAll((оп) => оп.map((о) => (о as HTMLOptionElement).value))
  const ошибки: string[] = []
  let меток = 0
  for (const к of значения) {
    await коллекторы(page).selectOption(к)
    await expect(page.locator('main svg[role="img"] g[data-section-id]').first()).toBeAttached()
    const наОси = await page.locator('main svg[role="img"] g[data-section-id]').evaluateAll(
      (гг, sel) =>
        гг.map((г) => {
          const з = г.querySelector(sel)
          return {
            id: Number(г.getAttribute('data-section-id')),
            цвет: з ? getComputedStyle(з).stroke : null,
          }
        }),
      ЗНАЧОК,
    )
    for (const м of наОси) {
      меток++
      const cls = класс.get(м.id)
      if (!cls) ошибки.push(`${м.id}: у участка нет уровня риска в /api/risks`)
      else if (м.цвет !== цвет[cls]) ошибки.push(`${м.id}: ${м.цвет} вместо ${цвет[cls]} (${cls})`)
    }
  }
  expect(меток, 'на схеме все участки из /api/risks').toBe(риски.length)
  expect(ошибки.slice(0, 10), `цвет значка равен цвету уровня на дашборде`).toEqual([])
})

test('US-05 сц. 3: фильтры складываются', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as Риск[]
  const класс = new Map(риски.map((r) => [r.section_id, r.risk_class]))
  const участки = (await (await page.request.get('/data/sections.json')).json()) as Участок[]

  // Коллектор, где под три условия подходит больше всего участков.
  const подходит = (у: Участок) =>
    класс.get(у.section_id) === 'high' && (у.kinds ?? []).includes('guardObject')
  const поКоллектору = new Map<number, number>()
  for (const у of участки)
    if (подходит(у)) поКоллектору.set(у.collector, (поКоллектору.get(у.collector) ?? 0) + 1)
  const [коллектор, ждём] = [...поКоллектору.entries()].sort((a, b) => b[1] - a[1])[0]
  const всего = участки.filter((у) => у.collector === коллектор).length

  await page.goto('/map')
  await коллекторы(page).selectOption(String(коллектор))
  await page.getByLabel('Уровень риска').selectOption({ label: 'Высокий' })
  await page.getByLabel('Тип объекта').selectOption({ label: 'Охраняемый объект' })
  // Район у заказчика один на весь парк — он назван на экране, выбирать нечего.
  await expect(page.getByText('Район: Район по эксплуатации')).toBeVisible()

  await expect(page.getByText(`${ждём} из ${всего} участков`)).toBeVisible()
  const наОси = await page
    .locator('main svg[role="img"] g[data-section-id]')
    .evaluateAll((гг) => гг.map((г) => Number(г.getAttribute('data-section-id'))))
  expect(наОси.length, 'меток на схеме столько же, сколько в счётчике').toBe(ждём)
  const лишние = наОси.filter((id) => !подходит(участки.find((у) => у.section_id === id)!))
  expect(лишние, 'на схеме только участки под все условия').toEqual([])
})

test('US-05 сц. 4: со схемы в карточку', async ({ page }) => {
  const риски = (await (await page.request.get('/api/risks')).json()) as Риск[]
  const участки = (await (await page.request.get('/data/sections.json')).json()) as Участок[]
  const высокий = new Set(риски.filter((r) => r.risk_class === 'high').map((r) => r.section_id))
  const коллектор = участки.find((у) => высокий.has(у.section_id))!.collector
  const рискиЭкрана = page.waitForResponse((r) => r.url().endsWith('/api/risks'))
  await page.goto('/map')
  await рискиЭкрана
  await коллекторы(page).selectOption(String(коллектор))
  await page.getByLabel('Уровень риска').selectOption({ label: 'Высокий' })
  // Густая ось кладёт соседние метки внахлёст (через ~1 px), сверху лежит нарисованная
  // последней — её диспетчер и видит, и нажимает.
  const метка = page.locator('main svg[role="img"] g[data-section-id]').last()
  const id = Number(await метка.getAttribute('data-section-id'))
  const риск = риски.find((r) => r.section_id === id)!
  expect(риск.risk_class, 'на схеме виден рискованный участок').toBe('high')

  await метка.locator(ЗНАЧОК).click()
  await expect(page).toHaveURL(new RegExp(`/objects/${id}$`))
  await expect(
    page.locator('section', { hasText: 'Уровень риска' }),
    'в карточке прогноз этого участка',
  ).toContainText(`вероятность ${риск.probability.toFixed(4)}`)
})
