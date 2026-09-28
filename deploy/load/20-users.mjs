// Нагрузочный сценарий: 20 диспетчеров одновременно (задачи 1.3 и 1.4, приёмка НФ-74).
//
// ТЗ разд. 11: «поддерживать одновременную работу не менее 20 пользователей без
// деградации производительности». Строка НФ-74: 20 параллельных сессий
// с открытым дашбордом, каждая выполняет сценарий из 20 действий; главный экран
// открывается не дольше 3 с, ответ API не дольше 3 с в 80 % запросов. Задача 1.4
// строже: 95-й перцентиль не хуже 2 с.
//
// Что делает одна сессия — то же, что браузер диспетчера, по запросам экранов
// из frontend/src:
//   1. Открывает главный экран: index.html, затем разом бандл, стили и шрифты,
//      затем разом /api/auth/me, /data/sections.json, /api/risks, /api/data-status
//      и полосу уведомлений. Время открытия — от первого запроса до последнего ответа.
//   2. Держит поток уведомлений GET /api/alerts/stream всю сессию, как вкладка.
//   3. Раз в THINK_S секунд делает следующее действие по кругу: схема, карточка
//      участка, журнал, карточка прогноза, заявки, карточка заявки, журнал
//      событий, дашборд. 20 действий вместе с открытием. Экрана «Источники»
//      в круге нет: роли диспетчера ОДС он закрыт (403), меню его не показывает.
//   4. Раз в минуту обновляет дашборд, как frontend/src/lib/poll.ts.
// Все 20 сессий стартуют разом: худший случай — смена открыла экраны одновременно
// и все пришли с одного адреса через общий шлюз, в одну зону лимита nginx.
//
// Без зависимостей, на Node 22+: как delivery/check-a11y.mjs. Не k6: у него
// лицензия AGPL-3.0, а перечень инструментов у нас спросят (docs/HLD.md разд. 7.3).
//
// Запуск:
//   BASE_URL=https://135.106.216.101 INSECURE=1 node deploy/load/20-users.mjs
//   USERS=20 DURATION_S=300 THINK_S=15 LOGINS=ods1 …   — умолчания
//   PASSWORD=… — входить по паролю через POST /api/auth/login, а не заголовком
//               X-User-Login (стенд заказчика с AUTH_TRUST_HEADER=0)
// Код возврата 1, если нарушен хоть один порог. Итог — таблица по методам
// и строки вердикта; её вставляем в docs/load-test.md, раздел «Нагрузка».

const env = process.env
const BASE = (env.BASE_URL || '').replace(/\/$/, '')
if (!BASE) {
  console.error('задайте BASE_URL, например https://135.106.216.101')
  process.exit(2)
}
if (env.INSECURE === '1') env.NODE_TLS_REJECT_UNAUTHORIZED = '0' // самоподписанный сертификат стенда
const USERS = +(env.USERS || 20)
const DURATION = +(env.DURATION_S || 300) * 1000
const THINK = +(env.THINK_S || 15) * 1000
const LOGINS = (env.LOGINS || 'ods1').split(',')
const ПОРОГ_ЭКРАН = 3000 // НФ-74 и НФ-20: главный экран не дольше 3 с
const ПОРОГ_P80 = 3000 // НФ-74 и НФ-24: API не дольше 3 с в 80 % запросов
const ПОРОГ_P95 = 2000 // задача 1.4: 95-й перцентиль не хуже 2 с

const замеры = new Map() // метод → [мс]
const ошибки = new Map() // метод → [«код путь»]
const экраны = [] // время открытия главного экрана, мс
let потоков = 0

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
// Имя метода без чисел: /api/objects/17/channels → /api/objects/{id}/channels.
const метод = (path) => path.split('?')[0].replace(/\/\d+(?=\/|$)/g, '/{id}').replace(/index-[^.]+/, 'index-*')

function запомнить(map, key, v) {
  if (!map.has(key)) map.set(key, [])
  map.get(key).push(v)
}

class Сессия {
  constructor(n) {
    this.n = n
    this.login = LOGINS[n % LOGINS.length]
    this.cookie = ''
    this.ids = { section: [], forecast: [], order: [] }
  }

  headers() {
    const h = { 'accept-encoding': 'gzip, br' }
    if (this.cookie) h.cookie = this.cookie
    else h['x-user-login'] = this.login
    return h
  }

  async get(path, init = {}) {
    const t0 = performance.now()
    let status = 0
    let body = null
    try {
      const r = await fetch(BASE + path, { headers: this.headers(), ...init })
      status = r.status
      const text = await r.text()
      if (r.ok && (r.headers.get('content-type') || '').includes('json')) body = JSON.parse(text)
      else if (r.ok) body = text
    } catch (e) {
      status = e.cause?.code || e.name
    }
    const ms = performance.now() - t0
    const key = метод(path)
    запомнить(замеры, key, ms)
    if (!(status >= 200 && status < 300)) запомнить(ошибки, key, `${status} ${path}`)
    return body
  }

  async войти() {
    if (!env.PASSWORD) return
    const r = await fetch(BASE + '/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ login: this.login, password: env.PASSWORD }),
    })
    if (!r.ok) throw new Error(`вход ${this.login}: ${r.status}`)
    this.cookie = (r.headers.get('set-cookie') || '').split(';')[0]
  }

  // Главный экран с пустым кэшем, в три волны, как их шлёт браузер.
  async открыть() {
    const t0 = performance.now()
    const html = (await this.get('/')) || ''
    const ассеты = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1])
    if (!ассеты.length) запомнить(ошибки, '/', 'в корне нет ссылок на /assets/ — отдаётся не приложение')
    await Promise.all(ассеты.map((a) => this.get(a)))
    const [, , risks] = await Promise.all([
      this.get('/api/auth/me'),
      this.get('/data/sections.json'),
      this.get('/api/risks'),
      this.get('/api/data-status'),
      this.get('/api/notifications?acked=false&limit=1000'),
    ])
    экраны.push(performance.now() - t0)
    if (Array.isArray(risks)) this.ids.section = risks.slice(0, 50).map((r) => r.section_id)
    this.asOf = Array.isArray(risks) && risks[0]?.as_of ? new Date(risks[0].as_of) : new Date()
  }

  // Поток уведомлений держится открытым до конца сессии, как у вкладки браузера.
  async поток(signal) {
    try {
      const r = await fetch(BASE + '/api/alerts/stream', { headers: this.headers(), signal })
      if (!r.ok) return запомнить(ошибки, '/api/alerts/stream', `${r.status}`)
      потоков++
      for await (const _ of r.body); // читаем, пока сессия не оборвёт
    } catch (e) {
      if (e.name !== 'AbortError') запомнить(ошибки, '/api/alerts/stream', e.cause?.code || e.name)
    }
  }

  действия() {
    const id = (k) => this.ids[k][this.n % Math.max(1, this.ids[k].length)]
    // Карточка участка берёт показания за сутки по датам, как ObjectCard.tsx.
    const до = this.asOf.toISOString().slice(0, 10)
    const от = new Date(this.asOf - 86400e3).toISOString().slice(0, 10)
    return [
      () => Promise.all([this.get('/api/risks'), this.get('/api/objects/tree')]),
      () =>
        id('section') &&
        Promise.all([
          this.get(`/api/objects/${id('section')}`),
          this.get(`/api/objects/${id('section')}/channels?limit=200`),
          this.get(`/api/objects/${id('section')}/readings?from=${от}&to=${до}`),
        ]),
      async () => {
        const f = await this.get('/api/forecasts')
        if (f?.items) this.ids.forecast = f.items.slice(0, 50).map((x) => x.forecast_id)
      },
      () => id('forecast') && this.get(`/api/forecasts/${id('forecast')}`),
      async () => {
        const [o] = await Promise.all([this.get('/api/orders'), this.get('/api/notifications?acked=false')])
        if (o?.items) this.ids.order = o.items.slice(0, 50).map((x) => x.order_id ?? x.id)
      },
      () => id('order') && this.get(`/api/orders/${id('order')}`),
      () => this.get(`/api/tech-events?sort=read_time&order=desc&limit=50&offset=0`),
      () => Promise.all([this.get('/api/risks'), this.get('/api/data-status')]),
    ]
  }

  async пройти() {
    await this.войти()
    const стоп = new AbortController()
    const поток = this.поток(стоп.signal)
    const конец = Date.now() + DURATION
    await this.открыть()
    const шаги = this.действия()
    let шаг = 0
    let опрос = Date.now() + 60_000
    while (Date.now() < конец) {
      await sleep(Math.min(THINK, Math.max(0, конец - Date.now())))
      if (Date.now() >= конец) break
      await шаги[шаг++ % шаги.length]()
      if (Date.now() >= опрос) {
        await Promise.all([this.get('/api/risks'), this.get('/api/data-status')])
        опрос += 60_000
      }
    }
    стоп.abort()
    await поток
    return шаг + 1
  }
}

const перцентиль = (a, p) => {
  const s = [...a].sort((x, y) => x - y)
  return s[Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1)]
}
const мс = (v) => (v === undefined ? '—' : String(Math.round(v)))

const t0 = Date.now()
console.log(`${USERS} сессий × ${DURATION / 1000} с, действие раз в ${THINK / 1000} с, против ${BASE}, логины ${LOGINS.join(',')}`)
const сессии = Array.from({ length: USERS }, (_, n) => new Сессия(n))
const действий = await Promise.all(сессии.map((s) => s.пройти()))

const все = [...замеры.entries()].filter(([k]) => k.startsWith('/api/'))
const api = все.flatMap(([, v]) => v)
console.log(`\nметод                                        запросов   p50   p80   p95   max  ошибок`)
for (const [k, v] of [...замеры.entries()].sort()) {
  console.log(
    `${k.padEnd(44)} ${String(v.length).padStart(8)} ${мс(перцентиль(v, 50)).padStart(5)} ${мс(перцентиль(v, 80)).padStart(5)} ${мс(перцентиль(v, 95)).padStart(5)} ${мс(Math.max(...v)).padStart(5)} ${String((ошибки.get(k) || []).length).padStart(7)}`,
  )
}
const всегоОшибок = [...ошибки.values()].reduce((n, v) => n + v.length, 0)
const p80 = перцентиль(api, 80)
const p95 = перцентиль(api, 95)
const худшийЭкран = Math.max(...экраны)
const итог = [
  [худшийЭкран <= ПОРОГ_ЭКРАН, `главный экран: худшее открытие из ${экраны.length} — ${мс(худшийЭкран)} мс, медиана ${мс(перцентиль(экраны, 50))} мс (порог ${ПОРОГ_ЭКРАН}, НФ-74)`],
  [p80 <= ПОРОГ_P80, `API: 80-й перцентиль ${мс(p80)} мс по ${api.length} запросам (порог ${ПОРОГ_P80}, НФ-74)`],
  [p95 <= ПОРОГ_P95, `API: 95-й перцентиль ${мс(p95)} мс (порог ${ПОРОГ_P95}, задача 1.4)`],
  [всегоОшибок === 0, `ошибок ${всегоОшибок}: ответы не 2xx и обрывы`],
  [потоков === USERS, `потоков уведомлений открыто ${потоков} из ${USERS}`],
]
console.log(`\nсессий ${USERS}, действий в сессии ${Math.min(...действий)}–${Math.max(...действий)}, прогон ${Math.round((Date.now() - t0) / 1000)} с`)
for (const [ok, текст] of итог) console.log(`${ok ? 'OK   ' : 'СБОЙ '} ${текст}`)
for (const [k, v] of ошибки) {
  const коды = {}
  for (const e of v) коды[e.split(' ')[0]] = (коды[e.split(' ')[0]] || 0) + 1
  console.log(`      ${k}: ${Object.entries(коды).map(([c, n]) => `${c} × ${n}`).join(', ')}`)
}
process.exit(итог.every(([ok]) => ok) ? 0 : 1)
