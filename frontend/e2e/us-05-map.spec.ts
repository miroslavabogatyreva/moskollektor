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
        await скрыть.evaluate((тег) => тег.remove())
        return { знак, фон, площадь }
      }
      await page.getByRole('button', { name: '+ приблизить' }).first().click()
      await page.waitForTimeout(300)
    }
  }
  throw new Error(`на оси нет одинокого значка «${слово}», ${чип ? 'чип' : 'густой режим'}`)
}

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
