// Мок GET /api/sensor-risk и GET /api/sensor-risk/summary по контракту SL.4
// (MOS-253) — для экранов SL.5 и SL.6, пока метода нет на стенде.
//
// Каппа (коллектор 15) — реальная картина из fixtures/sensor-risk-5657.json.
// На ПК632 при synthetic=1 пять high (267052, 267064, 267051, 267058, 267072)
// и один watch (267057) — так стенд отдавал 28.09.2026; при synthetic=0 high
// остаются два (267052, 267064), а 267051, 267058, 267072 становятся watch —
// ровно как лежит в файле. Остальные 15 коллекторов выдуманы из
// public/data/sections.json детерминированно: у «объекта Мю» 1 487 датчиков,
// как на стенде, с перекосом в плотные пикеты; у прочих — по два на участок.
//
// Сверх контракта мок понимает channel=<id>: по нему /map?channel= узнаёт коллектор.
//
// Форма ответа и пороги — как у бэкенда SL.4 (backend/app/api/schemas.py SensorRisk,
// SensorRiskSummary; backend/app/domain/sensor_risk.py): high от 0,5, watch от 0,25,
// limit по умолчанию 500, в top_collectors не больше пяти. Имени коллектора в строке
// сервер не отдаёт — collector_name здесь только для теста, в ответ он не уходит.
import { readFileSync } from 'node:fs'
import type { Page } from '@playwright/test'

type Level = 'high' | 'watch' | 'normal'
interface Reason {
  text: string
  weight: number
  kind: 'real' | 'synthetic' | 'plan'
}
interface Item {
  channel_id: number
  name: string
  sensor_kind: string
  node_id: number | null
  collector_id: number
  collector_name: string
  picket: number
  section_id: number | null
  score: number
  level: Level
  reasons: Reason[]
  equipment: Record<string, unknown> | null
}
interface Section {
  section_id: number
  collector: number
  collector_name?: string
  picket: number
}

export const КАППА = 15
export const МЮ = 3828
export const ДАТЧИКОВ_МЮ = 1487
const ПК632_ВЫСОКИЙ_С_ПАСПОРТОМ = new Set([267051, 267058, 267072])

const уровень = (score: number): Level =>
  score >= 0.5 ? 'high' : score >= 0.25 ? 'watch' : 'normal'

// Линейный конгруэнтный генератор: один и тот же парк на каждом прогоне.
function генератор(seed: number) {
  let x = seed
  return () => {
    x = (x * 1103515245 + 12345) % 2147483648
    return x / 2147483648
  }
}

function парк(): Item[] {
  const fixture = JSON.parse(readFileSync('e2e/fixtures/sensor-risk-5657.json', 'utf8')) as {
    items: Omit<Item, 'node_id' | 'collector_id' | 'collector_name'>[]
  }
  const sections = JSON.parse(readFileSync('public/data/sections.json', 'utf8')) as Section[]
  const items: Item[] = fixture.items.map((s) => ({
    ...s,
    node_id: 5657,
    collector_id: КАППА,
    collector_name: 'объект Каппа',
  }))
  const rnd = генератор(248)
  const виды = ['Состояние фазы', 'Датчик дыма', 'Датчик затопления', 'Газоанализатор', 'Дверь']
  let id = 400000
  const byCollector = new Map<number, Section[]>()
  for (const s of sections)
    byCollector.set(s.collector, [...(byCollector.get(s.collector) ?? []), s])
  for (const [c, secs] of [...byCollector.entries()].sort((a, b) => a[0] - b[0])) {
    if (c === КАППА) continue
    // Мю — 1 487 датчиков, степень 3 сгоняет их в первые участки списка: плотные пикеты.
    const участки =
      c === МЮ
        ? Array.from({ length: ДАТЧИКОВ_МЮ }, () => secs[Math.floor(rnd() ** 3 * secs.length)])
        : secs.flatMap((s) => [s, s])
    for (const s of участки) {
      const r = rnd()
      const real = +(r ** 6 * 0.55).toFixed(3)
      const synth = +(rnd() * 0.1).toFixed(3)
      const score = +(real + synth).toFixed(3)
      id += 1
      items.push({
        channel_id: id,
        node_id: null,
        name: `ДТ${id % 1000} ПК${s.picket}`,
        sensor_kind: виды[id % виды.length],
        collector_id: c,
        collector_name: s.collector_name ?? String(c),
        picket: s.picket,
        section_id: s.section_id,
        score,
        level: уровень(score),
        reasons: [
          {
            text: `отказов дольше 1 ч за 90 сут: ${Math.round(real * 30)}`,
            weight: real,
            kind: 'real',
          },
          {
            text: `выработано ${Math.round(synth * 1000)}% срока службы (синтетика)`,
            weight: synth,
            kind: 'synthetic',
          },
        ],
        equipment: {
          equipment_no: `SD-${id}`,
          manufacturer: 'Электроприбор',
          model_no: 'РКФ-12',
          in_service_from: '2016-05-01',
          service_life_years: 12,
          last_check_at: '2025-08-15',
          last_check_ok: true,
        },
      })
    }
  }
  return items
}

// Картина при выбранном переключателе. synthetic=0: паспорта нет, синтетические
// причины и их вес уходят; у Каппы уровни — как в файле.
function срез(items: Item[], synthetic: boolean): Item[] {
  return items
    .map((s): Item => {
      if (s.collector_id === КАППА) {
        if (!synthetic)
          return {
            ...s,
            reasons: s.reasons.filter((r) => r.kind !== 'synthetic'),
            equipment: null,
          }
        if (ПК632_ВЫСОКИЙ_С_ПАСПОРТОМ.has(s.channel_id))
          return { ...s, score: 0.542, level: 'high' }
        if (s.channel_id === 267057) return { ...s, score: 0.451, level: 'watch' }
        return s
      }
      if (synthetic) return s
      const synth = s.reasons
        .filter((r) => r.kind === 'synthetic')
        .reduce((a, r) => a + r.weight, 0)
      const score = +(s.score - synth).toFixed(3)
      return {
        ...s,
        score,
        level: уровень(score),
        reasons: s.reasons.filter((r) => r.kind !== 'synthetic'),
        equipment: null,
      }
    })
    .sort((a, b) => b.score - a.score || a.channel_id - b.channel_id)
}

export interface SensorMock {
  // Все адреса, которые экран спросил, — чтобы тест проверил параметры запроса.
  urls: string[]
  items: (synthetic: boolean) => Item[]
}

export async function mockSensorRisk(page: Page): Promise<SensorMock> {
  const все = парк()
  const cache = new Map<boolean, Item[]>()
  const items = (synthetic: boolean) => {
    if (!cache.has(synthetic)) cache.set(synthetic, срез(все, synthetic))
    return cache.get(synthetic)!
  }
  const urls: string[] = []
  const as_of = '2026-06-30T20:59:59+00:00'
  await page.route('**/api/sensor-risk**', (route) => {
    const url = new URL(route.request().url())
    urls.push(url.pathname + url.search)
    const q = url.searchParams
    const synthetic = q.get('synthetic') !== '0'
    const срезПарка = items(synthetic)
    if (url.pathname.endsWith('/summary')) {
      const high = new Map<number, { name: string; high: number }>()
      for (const s of срезПарка)
        if (s.level === 'high') {
          const g = high.get(s.collector_id) ?? { name: s.collector_name, high: 0 }
          g.high += 1
          high.set(s.collector_id, g)
        }
      const счёт = (l: Level) => срезПарка.filter((s) => s.level === l).length
      return route.fulfill({
        json: {
          synthetic,
          as_of,
          high: счёт('high'),
          watch: счёт('watch'),
          normal: счёт('normal'),
          collectors_with_high: high.size,
          top_collectors: [...high.entries()]
            .map(([collector_id, g]) => ({ collector_id, ...g }))
            .sort((a, b) => b.high - a.high || a.collector_id - b.collector_id)
            .slice(0, 5),
        },
      })
    }
    let отбор = срезПарка
    const collector = q.get('collector')
    const node = q.get('node')
    const channel = q.get('channel')
    const level = q.get('level')
    if (collector) отбор = отбор.filter((s) => s.collector_id === Number(collector))
    if (node) отбор = Number(node) === 5657 ? отбор.filter((s) => s.collector_id === КАППА) : []
    if (channel) отбор = отбор.filter((s) => s.channel_id === Number(channel))
    if (level) отбор = отбор.filter((s) => s.level === level)
    const offset = Number(q.get('offset') ?? 0)
    const limit = Number(q.get('limit') ?? 500)
    const имя = (id: number) => срезПарка.find((s) => s.collector_id === id)?.collector_name
    return route.fulfill({
      json: {
        synthetic,
        as_of,
        node: node ? Number(node) : null,
        node_name: node ? 'объект Каппа ДУ' : null,
        collector: collector ? Number(collector) : null,
        collector_name: collector ? (имя(Number(collector)) ?? null) : null,
        total: отбор.length,
        limit,
        offset,
        items: отбор.slice(offset, offset + limit).map(({ collector_name: _, ...s }) => s),
      },
    })
  })
  return { urls, items }
}
