#!/usr/bin/env node
// Проверка доступности трёх таблиц: дашборд, журнал, заявки (задача MOS-128).
//
// Зачем она появилась 22.09.2026. Работу по MOS-128 автор проверила клавиатурой
// и написала честно: Tab идёт по строкам, Enter открывает карточку, обходная
// ссылка появляется в фокусе. Всё это правда. Но два дефекта остались незамечены,
// потому что ни один из них не ломает ни одного нажатия:
//
//   1. `role="button"` на <th> вытесняет неявную роль columnheader. Атрибут
//      aria-sort в разметке есть и меняется при клике — а в дереве доступности
//      его нет вовсе: ARIA 1.2 разрешает aria-sort только на columnheader,
//      rowheader, row и gridcell, на роли button браузер его отбрасывает.
//      Замер 22.09.2026 на живом стенде: узлов columnheader 0, свойств sort 0.
//   2. `role="link"` на <tr> выводит строку из состава таблицы. Замер там же:
//      table 1, row 1, cell 0, link 205. Вместо 200 строк по 5 ячеек программа
//      чтения с экрана читает 200 ссылок с именем целиком:
//      «22.09.2026, 11:48:00 1727 Отказ датчика 0.87 24 ч» — без колонок.
//
// Отсюда правило этой проверки: она НЕ нажимает клавиши ради самого нажатия,
// она сравнивает дерево доступности с разметкой. Заголовков в <thead> столько
// же, сколько узлов columnheader; строк в <tbody> столько же, сколько узлов row
// минус шапка; ячеек больше нуля; строк с ролью link ровно ноль. Пара чисел из
// двух источников ловит расхождение, одно число — нет.
//
// Что она НЕ доказывает. Она не открывает NVDA и не слушает речь: она читает то
// же дерево, из которого диктор речь строит. Она ничего не говорит о контрасте
// и о цвете — это НФ-50, НФ-51 и НФ-53, они проверяются скриншотом.
//
// Строка приёмки — НФ-92, часть III (рекомендации, класс В). Её пришлось
// заводить 22.09.2026 отдельно: до этого дня в docs/acceptance-test.md не было
// ни одной строки про клавиатуру и программу чтения с экрана, и работа по
// MOS-128 три захода чинила требование, которого мы не записали. Часть именно
// III, а не II: часть II — это Регламент Москоллектора, WCAG туда не относится
// по определению части, и соседи по тому же основанию (НФ-50, НФ-51, НФ-53)
// стоят там же. Практическое следствие: на приёмке эту строку не спросят.
//
// Браузер свой и профиль свой: и MCP playwright, и MCP chrome-devtools держат
// профиль монопольно, вторая сессия получает отказ «Browser is already in use».
// Зависимостей нет: WebSocket и fetch лежат в стандартной поставке Node с 22-й
// версии, а Chrome понимает CDP сам.
//
// Запуск:
//     node delivery/check-a11y.mjs                      # стенд по умолчанию
//     BASE_URL=https://135.106.216.101 node delivery/check-a11y.mjs
//     BUNDLE=stand node delivery/check-a11y.mjs         # бандл, лежащий на стенде
//     node delivery/check-a11y.mjs --selfcheck          # без браузера и сети
//
// Код возврата 1, если упала хоть одна строка.

import http from 'node:http'
import https from 'node:https'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

const КОРЕНЬ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ДИСТ = path.join(КОРЕНЬ, 'frontend/dist')
const СТЕНД = new URL(process.env.BASE_URL || 'https://135.106.216.101')
const ЛОГИН = process.env.STAND_LOGIN || 'dispatcher1'
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

// Чей бандл проверяем. local — frontend/dist с диска (по умолчанию: так видно
// правку до выкладки). stand — бандл, который лежит на стенде прямо сейчас.
// Разница не теоретическая: 22.09.2026 проверка дала «проблем нет» на локальной
// сборке, а на стенде в ту же минуту лежал бандл без этих правок. Строку приёмки
// НФ-92 закрывает только BUNDLE=stand — иначе мы закрываем её на том, чего
// заказчику не отдали. Данные в обоих режимах живые, со стенда.
const БАНДЛ = process.env.BUNDLE === 'stand' ? 'stand' : 'local'

// Экраны, у которых есть таблица с заголовками. Карточки объекта здесь нет
// нарочно: её таблица в четыре строки, обходная ссылка ей не нужна.
const ЭКРАНЫ = [
  { имя: 'дашборд', путь: '/dashboard', конец: 'dashboard-table-end' },
  { имя: 'журнал', путь: '/log', конец: 'log-table-end' },
  { имя: 'заявки', путь: '/orders', конец: 'orders-table-end' },
]

let сбоев = 0
const строка = (исход, экран, что, чем) => {
  if (исход === 'СБОЙ') сбоев++
  console.log(`  ${исход.padEnd(8)} ${экран.padEnd(9)} ${что.padEnd(38)} ${чем}`)
}

// ── вердикт: единственное место, где решается «красная или зелёная» ───────────
// Вынесен отдельно, чтобы --selfcheck прогонял его на выдуманных числах без
// браузера. Возвращает список [исход, что, чем].
export function вердикт(з) {
  const итог = []
  const сравнить = (что, было, ждём, пояснение) =>
    итог.push([было === ждём ? 'OK' : 'СБОЙ', что, `${было} против ${ждём} ${пояснение}`])

  сравнить('заголовков колонок в дереве', з.columnheader, з.th, '(узлов columnheader против <th> в <thead>)')
  сравнить('строк таблицы в дереве', з.row, з.tr + 1, '(узлов row против строк <tbody> плюс шапка)')
  итог.push([з.cell > 0 ? 'OK' : 'СБОЙ', 'ячеек в дереве', `${з.cell} (должно быть больше нуля)`])
  итог.push([з.строкСоСсылкой === 0 ? 'OK' : 'СБОЙ', 'строк с ролью link',
    `${з.строкСоСсылкой} (должно быть ноль: role=link выводит строку из таблицы)`])
  итог.push([з.пропускДоФокуса?.видна === false ? 'OK' : 'СБОЙ', 'обходная ссылка до фокуса',
    з.пропускДоФокуса ? `${з.пропускДоФокуса.ш}×${з.пропускДоФокуса.в} px, clip-path ${з.пропускДоФокуса.clip}` : 'ссылки нет'])
  итог.push([з.пропускВФокусе?.видна === true ? 'OK' : 'СБОЙ', 'обходная ссылка в фокусе',
    з.пропускВФокусе ? `${з.пропускВФокусе.ш}×${з.пропускВФокусе.в} px, курсор ловит: ${з.пропускВФокусе.поймана}` : 'не сфокусировалась'])
  итог.push([з.конецПослеEnter === з.ждёмКонец ? 'OK' : 'СБОЙ', 'Enter на обходной ссылке',
    `фокус на «${з.конецПослеEnter}», ждали «${з.ждёмКонец}»`])
  return итог
}

// ── самопроверка вердикта: без браузера, без сети ────────────────────────────
function самопроверка() {
  const целое = {
    th: 5, columnheader: 5, tr: 200, row: 201, cell: 1000, строкСоСсылкой: 0,
    пропускДоФокуса: { видна: false, ш: 1, в: 1, clip: 'inset(50%)' },
    пропускВФокусе: { видна: true, ш: 149.6, в: 36.3, поймана: true },
    конецПослеEnter: 'log-table-end', ждёмКонец: 'log-table-end',
  }
  const красных = (з) => вердикт(з).filter(([и]) => и === 'СБОЙ').length
  const проба = [
    ['целое состояние', целое, 0],
    ['дефект 1: role=button на th съел columnheader', { ...целое, columnheader: 0 }, 1],
    ['дефект 2: role=link на tr вынес строки из таблицы',
      { ...целое, row: 1, cell: 0, строкСоСсылкой: 200 }, 3],
    ['дефект fe: inline style сделал ссылку видимой всегда',
      { ...целое, пропускДоФокуса: { видна: true, ш: 149.6, в: 36.3, clip: 'none' } }, 1],
    ['ссылка не появилась в фокусе',
      { ...целое, пропускВФокусе: { видна: false, ш: 1, в: 1, поймана: false } }, 1],
    ['Enter увёл не туда', { ...целое, конецПослеEnter: 'что-то другое' }, 1],
    ['обходной ссылки нет вовсе', { ...целое, пропускДоФокуса: null, пропускВФокусе: null }, 2],
  ]
  let плохо = 0
  for (const [имя, з, ждём] of проба) {
    const было = красных(з)
    const ок = было === ждём
    if (!ок) плохо++
    console.log(`  ${(ок ? 'OK' : 'СБОЙ').padEnd(8)} ${имя.padEnd(52)} красных ${было}, ждали ${ждём}`)
  }
  console.log(плохо === 0 ? '\nсамопроверка вердикта: проблем нет' : `\nсамопроверка вердикта: ${плохо} расхождений`)
  return плохо
}

// ── прокси: свой dist наружу, /api на стенд ──────────────────────────────────
function свободныйПорт() {
  return new Promise((ок) => {
    const s = net.createServer()
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => ок(p)) })
  })
}

const ТИПЫ = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml' }

function поднятьПрокси(порт) {
  const сервер = http.createServer((вход, ответ) => {
    if (БАНДЛ === 'stand' || new URL(вход.url, 'http://x').pathname.startsWith('/api/')) {
      const наружу = https.request(
        { host: СТЕНД.hostname, port: СТЕНД.port || 443, path: вход.url, method: вход.method,
          rejectUnauthorized: false,
          headers: { ...вход.headers, host: СТЕНД.hostname, 'X-User-Login': ЛОГИН } },
        (r) => { ответ.writeHead(r.statusCode, r.headers); r.pipe(ответ) })
      наружу.on('error', (e) => { ответ.writeHead(502); ответ.end(String(e)) })
      вход.pipe(наружу)
      return
    }
    let файл = path.join(ДИСТ, new URL(вход.url, 'http://x').pathname)
    if (!fs.existsSync(файл) || fs.statSync(файл).isDirectory()) файл = path.join(ДИСТ, 'index.html')
    ответ.writeHead(200, { 'content-type': ТИПЫ[path.extname(файл)] ?? 'application/octet-stream' })
    ответ.end(fs.readFileSync(файл))
  })
  return new Promise((ок) => сервер.listen(порт, '127.0.0.1', () => ок(сервер)))
}

// ── Chrome по CDP: одна вкладка, настоящие нажатия ───────────────────────────
async function поднятьChrome(порт, профиль) {
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

class Вкладка {
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

// ── замер одного экрана ──────────────────────────────────────────────────────
const МЕРА_ССЫЛКИ = `
  const a=[...document.querySelectorAll('a')].find(a=>(a.textContent||'').includes('Пропустить'))
  if(!a) return null
  const r=a.getBoundingClientRect(), s=getComputedStyle(a)
  const т=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2)
  return {ш:+r.width.toFixed(1), в:+r.height.toFixed(1), clip:s.clipPath,
          поймана: т===a || a.contains(т),
          видна: r.width>4 && r.height>4 && s.clipPath==='none'}`

async function замерить(в, порт, экран) {
  await в.перейти(`http://127.0.0.1:${порт}${экран.путь}`)
  // Ждём не таймером, а появлением строк: таблица приезжает из API стенда.
  for (let i = 0; i < 100; i++) {
    if (await в.считать('return document.querySelectorAll("tbody tr").length > 0')) break
    await new Promise((о) => setTimeout(о, 100))
  }
  const разметка = await в.считать(`return {
    th: document.querySelectorAll('thead th').length,
    tr: document.querySelectorAll('tbody tr').length,
    строкСоСсылкой: document.querySelectorAll('tbody tr[role="link"]').length }`)

  const пропускДоФокуса = await в.считать(МЕРА_ССЫЛКИ)
  await в.считать(`const a=[...document.querySelectorAll('a')].find(a=>(a.textContent||'').includes('Пропустить')); a?.focus(); return 1`)
  const пропускВФокусе = await в.считать(МЕРА_ССЫЛКИ)
  await в.клавиша('Enter')
  const конецПослеEnter = await в.считать('return document.activeElement?.id || document.activeElement?.tagName')

  const узлы = (await в.команда('Accessibility.getFullAXTree')).nodes
  const счёт = (роль) => узлы.filter((у) => у.role?.value === роль).length
  return { ...разметка, пропускДоФокуса, пропускВФокусе, конецПослеEnter, ждёмКонец: экран.конец,
    columnheader: счёт('columnheader'), row: счёт('row'), cell: счёт('cell') }
}

// ── прогон ───────────────────────────────────────────────────────────────────
if (process.argv.includes('--selfcheck')) process.exit(самопроверка() === 0 ? 0 : 1)

if (БАНДЛ === 'local' && !fs.existsSync(path.join(ДИСТ, 'index.html')))
  throw new Error(`нет сборки фронта: ${ДИСТ}/index.html. Сначала npm --prefix frontend run build`)

const порт = await свободныйПорт()
const отладка = await свободныйПорт()
const профиль = fs.mkdtempSync(path.join(process.env.TMPDIR || '/tmp', 'a11y-chrome-'))
const сервер = await поднятьПрокси(порт)
const chrome = await поднятьChrome(отладка, профиль)
const в = await Вкладка.открыть(отладка)

console.log(БАНДЛ === 'stand'
  ? `проверка доступности таблиц: БАНДЛ СО СТЕНДА ${СТЕНД.origin}, данные оттуда же\n`
  : `проверка доступности таблиц: локальная сборка ${ДИСТ}, данные со стенда ${СТЕНД.origin}\n  строку НФ-92 этот режим не закрывает, для неё нужен BUNDLE=stand\n`)
try {
  for (const экран of ЭКРАНЫ) {
    const з = await замерить(в, порт, экран)
    for (const [исход, что, чем] of вердикт(з)) строка(исход, экран.имя, что, чем)
  }
} finally {
  // Уборка не имеет права ронять проверку. 22.09.2026 первый прогон в составе
  // check-all.sh дал «УПАЛА НФ-92», и красным он был не от дефекта, а от
  // rm ENOTEMPTY: Chrome после kill ещё дописывал файлы профиля. Красная
  // строка по неверной причине лжёт ровно так же, как зелёная по неверной —
  // по ней чинят не то. Поэтому ждём выхода процесса и глотаем ошибку rm.
  chrome.kill()
  await new Promise((о) => { chrome.once('exit', о); setTimeout(о, 3000) })
  сервер.close()
  try { fs.rmSync(профиль, { recursive: true, force: true }) } catch {}
}

// Режим называется и в итоге, не только в шапке: в протокол приёмки копируют
// строки результата, первую строку при этом отрезают, и BUNDLE=local тогда
// не отличить от BUNDLE=stand. А именно подмена режима тихо гасит НФ-92.
console.log(`\n[бандл: ${БАНДЛ}] ` + (сбоев === 0 ? 'проблем нет' : `сбоев: ${сбоев}`))
process.exit(сбоев === 0 ? 0 : 1)
