// US-27 «Забрать риски, прогнозы и заявки через REST API» — docs/user-stories.md,
// MOS-212, приёмка М-14…М-17, Ф-55.
//
// Смежная система в тесте — отдельный request context, а не page.request: у неё
// нет ни куки браузера, ни заголовков, которые Playwright подставляет странице.
// Учётную запись она называет сама заголовком X-User-Login (на стенде
// AUTH_TRUST_HEADER=1) или не называет вовсе — это сценарий 2.
import { expect, test, request as pwRequest, type APIRequestContext } from '@playwright/test'

async function система(baseURL: string | undefined, login?: string): Promise<APIRequestContext> {
  return pwRequest.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: login ? { 'X-User-Login': login } : {},
  })
}

interface Risk {
  section_id: number
  probability: number
}

// Сценарий 1 назван для диспетчера ОДС (ods1 видит весь парк). Техник tech1 видит
// только свой комплекс — на нём видно, что API отдаёт столько же, сколько экран
// той же роли, а не весь парк любому, кто спросил.
for (const login of ['ods1', 'tech1']) {
  test.describe(`под ${login}`, () => {
    test.use({ extraHTTPHeaders: { 'X-User-Login': login } })

    test(`US-27 сц. 1: те же числа, что на экране (${login})`, async ({ page, baseURL }) => {
      await page.goto('/dashboard')
      const плитка = page.locator('article', { hasText: 'Участков в расчёте' })
      await expect(плитка).toBeVisible()
      const n = Number((await плитка.locator('div').first().innerText()).trim())
      const строки = page.locator('main table tbody tr')
      await expect(строки).toHaveCount(n)

      // С экрана: номер участка после точки в столбце «Объект» и вероятность
      // в столбце «Вероятность» — процент с одним знаком: «91,5 %» (дашборд 28.09.2026).
      const наЭкране = new Map<number, string>(
        await строки.evaluateAll((trs) =>
          trs.map((tr) => {
            const td = tr.querySelectorAll('td')
            return [
              Number(td[1].textContent!.match(/·\s*(\d+)\s*$/)![1]),
              td[3].textContent!.trim(),
            ] as [number, string]
          }),
        ),
      )
      expect(наЭкране.size, 'участки на экране не повторяются').toBe(n)

      const api = await система(baseURL, login)
      const r = await api.get('/api/risks')
      expect(r.status()).toBe(200)
      expect(r.headers()['content-type']).toContain('application/json')
      const риски = (await r.json()) as Risk[]
      expect(риски.length, `в ответе столько участков, сколько на экране (${n})`).toBe(n)
      for (const x of риски)
        expect(
          `${(x.probability * 100).toFixed(1).replace('.', ',')} %`,
          `участок ${x.section_id}: вероятность в API и на экране`,
        ).toBe(наЭкране.get(x.section_id))
      await api.dispose()
      test.info().annotations.push({ type: 'замер', description: `${login}: ${n} участков` })
    })
  })
}

test('US-27 сц. 2: без учётной записи — отказ', async ({ baseURL }) => {
  const api = await система(baseURL)
  const r = await api.get('/api/risks')
  expect(r.status()).toBe(401)
  expect(r.headers()['content-type']).toContain('application/json')
  const тело = (await r.json()) as { detail?: unknown }
  expect(typeof тело.detail, 'причина отказа строкой в поле detail').toBe('string')
  expect((тело.detail as string).length).toBeGreaterThan(0)
  expect(await r.text(), 'в отказе нет данных о рисках').not.toContain('probability')
  await api.dispose()
})

// Описание API — /openapi.json, его же рисует /docs. Явных examples в нём нет,
// пример ответа в /docs Swagger UI строит из схемы ответа: у объекта — все его
// properties, у массива — один элемент. Тест строит тот же пример и сравнивает
// с живым ответом состав полей в обе стороны: лишнее поле в ответе — не описано,
// недостающее — описание обещает то, чего нет.
type Схема = {
  $ref?: string
  type?: string
  properties?: Record<string, Схема>
  items?: Схема
  anyOf?: Схема[]
}
type Описание = {
  paths: Record<string, Record<string, any>>
  components: { schemas: Record<string, Схема> }
}

function раскрыть(d: Описание, s: Схема): Схема {
  while (s.$ref) s = d.components.schemas[s.$ref.split('/').pop()!]
  if (s.anyOf) return раскрыть(d, s.anyOf.find((x) => x.type !== 'null') ?? s.anyOf[0])
  return s
}

function пример(d: Описание, s: Схема): unknown {
  s = раскрыть(d, s)
  if (s.properties)
    return Object.fromEntries(Object.entries(s.properties).map(([k, v]) => [k, пример(d, v)]))
  if (s.type === 'array' && s.items) return [пример(d, s.items)]
  return s.type ?? 'значение'
}

function сверить(образец: unknown, ответ: unknown, путь: string, расхождения: string[]) {
  if (Array.isArray(образец)) {
    if (Array.isArray(ответ) && ответ.length > 0)
      сверить(образец[0], ответ[0], `${путь}[0]`, расхождения)
    return
  }
  if (образец === null || typeof образец !== 'object') return
  if (ответ === null || typeof ответ !== 'object') return
  const ждём = Object.keys(образец).sort()
  const есть = Object.keys(ответ).sort()
  for (const k of ждём)
    if (!есть.includes(k)) расхождения.push(`${путь}.${k}: в описании есть, в ответе нет`)
  for (const k of есть)
    if (!ждём.includes(k)) расхождения.push(`${путь}.${k}: в ответе есть, в описании нет`)
  for (const k of ждём)
    if (есть.includes(k))
      сверить(
        (образец as Record<string, unknown>)[k],
        (ответ as Record<string, unknown>)[k],
        `${путь}.${k}`,
        расхождения,
      )
}

test('US-27 сц. 3: методы описаны', async ({ baseURL }) => {
  const api = await система(baseURL, 'ods1')
  const d = (await (await api.get('/openapi.json')).json()) as Описание
  // Риски, прогнозы за период и заявки — три ресурса истории.
  const запросы: Record<string, Record<string, string>> = {
    '/api/risks': {},
    '/api/forecasts': { limit: '5' },
    '/api/orders': { limit: '5' },
  }
  const расхождения: string[] = []
  for (const [путь, параметры] of Object.entries(запросы)) {
    const op = d.paths[путь]?.get
    expect(op, `${путь} есть в описании`).toBeTruthy()
    // Параметры примера — только те, что описание объявляет.
    const объявлены = new Set((op.parameters ?? []).map((p: { name: string }) => p.name))
    for (const k of Object.keys(параметры))
      expect(объявлены.has(k), `${путь}: параметр ${k} описан`).toBe(true)
    const схема = op.responses?.['200']?.content?.['application/json']?.schema as Схема | undefined
    expect(схема, `${путь}: у ответа 200 есть схема`).toBeTruthy()
    const образец = пример(d, схема!)

    const r = await api.get(путь, { params: параметры })
    expect(r.status(), путь).toBe(200)
    const ответ = await r.json()
    const записи = Array.isArray(ответ) ? ответ : ответ.items
    expect(записи.length, `${путь}: ответ не пустой, есть что сверять`).toBeGreaterThan(0)
    сверить(образец, ответ, путь, расхождения)
  }
  expect(расхождения, 'состав полей ответа совпадает с примером из описания').toEqual([])
  await api.dispose()
})
