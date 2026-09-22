// Общая обвязка браузерных проверок delivery/: прокси на стенд, свой Chrome по CDP
// и вкладка с настоящими нажатиями. Вынесена из check-a11y.mjs 22.09.2026, когда
// появилась вторая проверка экрана (check-map.mjs): две копии одной обвязки
// расходятся молча. Зависимостей нет — WebSocket и fetch в Node с 22-й версии.

import http from 'node:http'
import https from 'node:https'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { spawn } from 'node:child_process'

export const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

// ── прокси: свой dist наружу, /api на стенд ──────────────────────────────────
export function свободныйПорт() {
  return new Promise((ок) => {
    const s = net.createServer()
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => ок(p)) })
  })
}

const ТИПЫ = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml' }

export function поднятьПрокси(порт, { бандл, дист, стенд, логин }) {
  const сервер = http.createServer((вход, ответ) => {
    if (бандл === 'stand' || new URL(вход.url, 'http://x').pathname.startsWith('/api/')) {
      const наружу = https.request(
        { host: стенд.hostname, port: стенд.port || 443, path: вход.url, method: вход.method,
          rejectUnauthorized: false,
          headers: { ...вход.headers, host: стенд.hostname, 'X-User-Login': логин } },
        (r) => { ответ.writeHead(r.statusCode, r.headers); r.pipe(ответ) })
      наружу.on('error', (e) => { ответ.writeHead(502); ответ.end(String(e)) })
      вход.pipe(наружу)
      return
    }
    let файл = path.join(дист, new URL(вход.url, 'http://x').pathname)
    if (!fs.existsSync(файл) || fs.statSync(файл).isDirectory()) файл = path.join(дист, 'index.html')
    ответ.writeHead(200, { 'content-type': ТИПЫ[path.extname(файл)] ?? 'application/octet-stream' })
    ответ.end(fs.readFileSync(файл))
  })
  return new Promise((ок) => сервер.listen(порт, '127.0.0.1', () => ок(сервер)))
}

// ── Chrome по CDP: одна вкладка, настоящие нажатия ───────────────────────────
export async function поднятьChrome(порт, профиль) {
  const п = spawn(CHROME, [`--remote-debugging-port=${порт}`, `--user-data-dir=${профиль}`,
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1400,1000', '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' })
  // spawn сообщает о ненайденном файле событием, а не исключением: без этого
  // обработчика запуск с неверным CHROME вываливал голый «spawn … ENOENT»
  // стеком Node, и docs/install.md обещал сообщение, которого не было.
  let сбойЗапуска = null
  п.on('error', (e) => { сбойЗапуска = e })
  for (let i = 0; i < 100; i++) {
    if (сбойЗапуска)
      throw new Error(`Chrome не запустился: ${сбойЗапуска.code} по пути ${CHROME}. `
        + `Путь задаётся переменной CHROME=, например CHROME=/usr/bin/google-chrome`)
    try { if ((await fetch(`http://127.0.0.1:${порт}/json/version`)).ok) return п } catch {}
    await new Promise((о) => setTimeout(о, 100))
  }
  throw new Error(`Chrome не ответил за 10 секунд (путь ${CHROME}, задаётся переменной CHROME=)`)
}

export class Вкладка {
  constructor() { this.n = 0; this.ждут = new Map(); this.события = [] }

  static async открыть(порт) {
    const в = new Вкладка()
    const { webSocketDebuggerUrl } = await (await fetch(`http://127.0.0.1:${порт}/json/version`)).json()
    в.сокет = new WebSocket(webSocketDebuggerUrl)
    await new Promise((ок, нет) => { в.сокет.onopen = ок; в.сокет.onerror = нет })
    в.сокет.onmessage = (m) => {
      const с = JSON.parse(m.data)
      if (с.id && в.ждут.has(с.id)) {
        const [ок, нет] = в.ждут.get(с.id); в.ждут.delete(с.id)
        с.error ? нет(new Error(JSON.stringify(с.error))) : ок(с.result)
      } else if (с.method) в.события.push(с)
    }
    const { targetId } = await в.команда('Target.createTarget', { url: 'about:blank' })
    const { sessionId } = await в.команда('Target.attachToTarget', { targetId, flatten: true })
    в.сессия = sessionId
    await в.команда('Page.enable')
    await в.команда('Runtime.enable')
    await в.команда('Accessibility.enable')
    return в
  }

  команда(метод, параметры = {}) {
    const id = ++this.n
    const тело = { id, method: метод, params: параметры }
    if (this.сессия && !метод.startsWith('Target.')) тело.sessionId = this.сессия
    this.сокет.send(JSON.stringify(тело))
    return new Promise((ок, нет) => this.ждут.set(id, [ок, нет]))
  }

  async перейти(url) {
    this.события = []
    await this.команда('Page.navigate', { url })
    for (let i = 0; i < 300; i++) {
      if (this.события.some((с) => с.method === 'Page.loadEventFired')) return
      await new Promise((о) => setTimeout(о, 50))
    }
    throw new Error(`страница ${url} не загрузилась`)
  }

  async считать(выражение) {
    const r = await this.команда('Runtime.evaluate',
      { expression: `(async()=>{${выражение}})()`, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(String(r.exceptionDetails.exception?.description).slice(0, 300))
    return r.result.value
  }

  // Настоящее нажатие, а не dispatchEvent: фокус двигает браузер, и только так
  // видно порядок обхода. dispatchEvent из страницы фокус не переносит вовсе.
  async клавиша(имя) {
    const код = имя === 'Tab' ? 9 : 13
    const общее = { key: имя, code: имя, windowsVirtualKeyCode: код, nativeVirtualKeyCode: код }
    await this.команда('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...общее })
    await this.команда('Input.dispatchKeyEvent', { type: 'char', text: имя === 'Enter' ? '\r' : '\t', ...общее })
    await this.команда('Input.dispatchKeyEvent', { type: 'keyUp', ...общее })
    await new Promise((о) => setTimeout(о, 80))
  }
}
