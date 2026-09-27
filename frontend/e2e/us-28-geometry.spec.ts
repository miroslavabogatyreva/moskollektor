// US-28 «Забрать геометрию участков» (MOS-213), строки приёмки Ф-80, Ф-81.
// Экрана у истории нет: смежная ГИС ходит в GET /api/geo/sections сама, поэтому оба
// сценария проверяют ответ API фикстурой request, а XML разбирает DOMParser браузера —
// «стандартный парсер» из Ф-80.
//
// Участок берём первым в GET /api/risks: ГИС кладёт на карту именно риски, и верхний
// в списке участок у диспетчера есть всегда. Геометрия синтетическая (MOS-45), поэтому
// «координаты совпадают» — это совпадение GeoJSON и WKT между собой, а не с данными
// заказчика: «Координаты предоставлены не будут» (ответ 19.09.2026).
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

type Точка = [number, number]

async function выбранныйУчасток(request: APIRequestContext): Promise<number> {
  const r = await request.get('/api/risks')
  expect(r.status()).toBe(200)
  const [первый] = (await r.json()) as { section_id: number }[]
  return первый.section_id
}

// GeoJSON LineString и MultiLineString — к одному виду: список линий из точек.
function линииGeoJson(g: { type: string; coordinates: unknown }): Точка[][] {
  if (g.type === 'LineString') return [g.coordinates as Точка[]]
  if (g.type === 'MultiLineString') return g.coordinates as Точка[][]
  throw new Error(`неожиданный тип геометрии ${g.type}`)
}

// WKT LINESTRING(x y,…) и MULTILINESTRING((x y,…),(…)) — к тому же виду.
function линииWkt(wkt: string): Точка[][] {
  const m = wkt.match(/^(MULTILINESTRING|LINESTRING)\s*\((.*)\)$/)
  if (!m) throw new Error(`не WKT линии: ${wkt}`)
  const части =
    m[1] === 'LINESTRING' ? [m[2]] : [...m[2].matchAll(/\(([^()]*)\)/g)].map((x) => x[1])
  return части.map((ч) => ч.split(',').map((p) => p.trim().split(/\s+/).map(Number) as Точка))
}

// Листья JSON и листья XML в порядке документа — строками, чтобы сравнить один к одному.
function листьяJson(v: unknown): string[] {
  if (v === null) return ['null']
  if (Array.isArray(v)) return v.flatMap(листьяJson)
  if (typeof v === 'object') return Object.values(v as object).flatMap(листьяJson)
  return [String(v)]
}

async function листьяXml(page: Page, xml: string): Promise<string[]> {
  return page.evaluate((text) => {
    const doc = new DOMParser().parseFromString(text, 'application/xml')
    if (doc.getElementsByTagName('parsererror').length) throw new Error('XML не разобран')
    const out: string[] = []
    const обход = (el: Element) => {
      if (el.children.length === 0)
        out.push(el.getAttribute('nil') === 'true' ? 'null' : (el.textContent ?? ''))
      else for (const c of Array.from(el.children)) обход(c)
    }
    обход(doc.documentElement)
    return out
  }, xml)
}

test('US-28 сц. 1: два формата — одна геометрия', async ({ request }) => {
  const id = await выбранныйУчасток(request)

  const gj = await request.get(`/api/geo/sections?geometry=geojson&section_id=${id}`)
  expect(gj.status()).toBe(200)
  expect(gj.headers()['content-type']).toContain('application/geo+json')
  const fc = await gj.json()
  expect(fc.type).toBe('FeatureCollection')
  expect(fc.features).toHaveLength(1)
  expect(fc.features[0].properties.section_id).toBe(id)

  const wk = await request.get(`/api/geo/sections?geometry=wkt&section_id=${id}`)
  expect(wk.status()).toBe(200)
  const w = await wk.json()
  expect(w.items).toHaveLength(1)
  expect(w.items[0].section_id).toBe(id)

  // Координаты совпадают точно, до последнего знака, а не «примерно».
  const изGeoJson = линииGeoJson(fc.features[0].geometry)
  expect(изGeoJson.flat().length).toBeGreaterThanOrEqual(2)
  expect(линииWkt(w.items[0].wkt)).toEqual(изGeoJson)

  // Система координат названа в обоих ответах, одна и та же.
  expect(fc.crs).toMatch(/^EPSG:\d+$/)
  expect(w.crs).toBe(fc.crs)
  // И сказано, что линии нарисованы нами, а не сняты с местности.
  expect(fc.geometry_source).toBe('synthetic')
  expect(w.geometry_source).toBe('synthetic')
})

test('US-28 сц. 2: XML по запросу', async ({ request, page }) => {
  const id = await выбранныйУчасток(request)
  for (const geometry of ['geojson', 'wkt']) {
    const url = `/api/geo/sections?geometry=${geometry}&section_id=${id}`
    const json = await request.get(url, { headers: { Accept: 'application/json' } })
    expect(json.status()).toBe(200)
    const xml = await request.get(url, { headers: { Accept: 'application/xml' } })
    expect(xml.status()).toBe(200)
    expect(xml.headers()['content-type']).toContain('application/xml')

    const ждём = листьяJson(await json.json())
    expect(ждём.length).toBeGreaterThan(4)
    expect(await листьяXml(page, await xml.text()), `geometry=${geometry}`).toEqual(ждём)
  }
})
