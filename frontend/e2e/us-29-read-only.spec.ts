// US-29 «Убедиться, что сервис только читает» — docs/user-stories.md, MOS-214,
// приёмка Ф-74, НФ-69.
//
// Тест перебирает в /openapi.json стенда все методы, которые меняют данные
// (POST, PUT, PATCH, DELETE), и сверяет их со списком ниже. В списке у каждого
// метода — куда он пишет, проверено по коду 27.09.2026. Появился новый пишущий
// метод — тест падает, пока метод не попадёт в список с ответом «куда пишет»;
// ответ «в СМВУ» или «на оборудование» в список не кладём: это нарушение ТЗ разд. 5.
//
// Сверх записей в таблице каждый запрос, кроме /health и приёма потока, пишет
// строку в audit.user_action (промежуточный слой, backend/app/api/main.py) —
// это тоже база сервиса, в списке не повторяем.
import { expect, test } from '@playwright/test'

// Схемы нашей базы — все CREATE SCHEMA в db/migrations: всё, что не в них, — не база
// сервиса. smvu здесь — наша копия журнала СМВУ, а не сама СМВУ.
const СХЕМЫ_СЕРВИСА = [
  'asset',
  'audit',
  'ext',
  'feat',
  'geo',
  'load',
  'maint',
  'permit',
  'pred',
  'ref',
  'smvu',
]

const ПИШУЩИЕ: Record<string, { таблицы: string[]; что: string }> = {
  'POST /api/auth/login': {
    таблицы: ['ref.app_user', 'ref.user_role', 'ref.user_scope'],
    что: 'вход; учётку каталога сверяет с группами (backend/app/api/auth.py, _sync_ldap_user). В каталог LDAP — только bind и поиск групп (backend/app/auth/ldap.py), кука mk_session — браузеру',
  },
  'POST /api/auth/logout': {
    таблицы: [],
    что: 'выход: стирает куку mk_session, в таблицы не пишет',
  },
  'POST /api/auth/directory/check': {
    таблицы: [],
    что: 'анонимный bind к каталогу с таймаутом 3 с — проверка связи, в каталог и в базу не пишет',
  },
  'PATCH /api/auth/users/{login}': {
    таблицы: ['ref.app_user'],
    что: 'блокировка пользователя сервиса, поле is_active (US-30)',
  },
  'POST /api/forecasts/{forecast_id}/outcome': {
    таблицы: ['pred.forecast_outcome'],
    что: 'исход прогноза: подтвердилось или ложная с причиной (US-10)',
  },
  'POST /api/forecasts/{forecast_id}/feedback': {
    таблицы: ['pred.feedback'],
    что: 'решение диспетчера по прогнозу (US-09)',
  },
  'PUT /api/settings/{key}': {
    таблицы: ['ref.app_setting'],
    что: 'настройка сервиса: пороги, горизонт, окно веса (US-23)',
  },
  'POST /api/ingest/readings': {
    таблицы: ['smvu.reading', 'load.batch'],
    что: 'приём потока СМВУ в нашу копию: СМВУ шлёт нам, а не мы ей (эмулятор, backend/app/ingest/readings.py)',
  },
  'POST /api/ingest/ods-events': {
    таблицы: ['maint.ods_event'],
    что: 'приём событий журнала ОДС в нашу копию (US-12 сц. 5)',
  },
  'POST /api/notifications/{notification_id}/ack': {
    таблицы: ['maint.notification'],
    что: 'квитирование уведомления: acked_by, acked_at',
  },
  'POST /api/permits': {
    таблицы: ['permit.permit', 'permit.location', 'ref.object_xref'],
    что: 'эмулятор реестра нарядов: открыть наряд-допуск на участке (US-13)',
  },
  'POST /api/permits/{permit_id}/close': {
    таблицы: ['permit.permit'],
    что: 'эмулятор реестра нарядов: закрыть наряд',
  },
}

// Слова пульта управления в пути, имени операции и заголовке метода.
const УПРАВЛЕНИЕ =
  /command|control|actuat|relay|switch|reset|reboot|device|equipment|команд|управлен|оборудован/i

test('US-29 сц. 1: ни одного метода управления', async ({ request }) => {
  const r = await request.get('/openapi.json')
  expect(r.status()).toBe(200)
  const d = (await r.json()) as {
    paths: Record<string, Record<string, { operationId?: string; summary?: string }>>
  }

  const пишущие: string[] = []
  const всего: string[] = []
  for (const [путь, методы] of Object.entries(d.paths))
    for (const [метод, op] of Object.entries(методы)) {
      const имя = `${метод.toUpperCase()} ${путь}`
      всего.push(имя)
      if (['post', 'put', 'patch', 'delete'].includes(метод)) пишущие.push(имя)
      expect(
        `${путь} ${op.operationId ?? ''} ${op.summary ?? ''}`,
        `${имя}: в имени метода нет слов пульта управления`,
      ).not.toMatch(УПРАВЛЕНИЕ)
    }

  expect(
    пишущие.filter((m) => !(m in ПИШУЩИЕ)),
    'новый метод меняет данные: впиши его в ПИШУЩИЕ с таблицами, куда он пишет',
  ).toEqual([])
  expect(
    Object.keys(ПИШУЩИЕ).filter((m) => !пишущие.includes(m)),
    'метода из списка больше нет в описании API: убери его из ПИШУЩИЕ',
  ).toEqual([])
  for (const [имя, { таблицы }] of Object.entries(ПИШУЩИЕ))
    for (const т of таблицы)
      expect(СХЕМЫ_СЕРВИСА, `${имя} пишет в ${т} — это база сервиса`).toContain(т.split('.')[0])

  test.info().annotations.push({
    type: 'замер',
    description: `методов в описании: ${всего.length}; меняют данные: ${пишущие.length}, все пишут только в базу сервиса`,
  })
})

// Вторая половина Ф-74 — экраны. Обходить их браузером из облачной сессии нельзя:
// прокси рвёт навигацию (ERR_TOO_MANY_RETRIES, 28.09.2026). Поэтому читаем бандл,
// выложенный на стенд, — это ровно то, что исполняет браузер диспетчера. Минификатор
// оставляет вызовы в виде W(`/api/…/${e}/ack`,{method:`POST`}), путь и метод видны.
test('US-29 сц. 2: экраны не шлют ничего, кроме методов из списка', async ({ request }) => {
  const html = await (await request.get('/')).text()
  const бандлы = [...html.matchAll(/src="(\/assets\/[^"]+\.js)"/g)].map((m) => m[1])
  expect(бандлы.length, 'в index.html есть бандл').toBeGreaterThan(0)

  const вызовы: string[] = []
  const чужие: string[] = []
  const слова: string[] = []
  for (const путь of бандлы) {
    const js = await (await request.get(путь)).text()
    for (const m of js.matchAll(
      /\(\s*[`'"]([^`'"]*)[`'"]\s*,\s*\{[^{}]*?method:\s*[`'"](POST|PUT|PATCH|DELETE)[`'"]/g,
    ))
      вызовы.push(`${m[2]} ${m[1].replace(/\$\{[^}]*\}/g, '{}').split('?')[0]}`)
    // Абсолютные адреса: всё, кроме пространств имён SVG/MathML/XHTML, — запрос мимо стенда.
    for (const m of js.matchAll(/https?:\/\/[^\s"'`)]+/g))
      if (!m[0].startsWith('http://www.w3.org/')) чужие.push(m[0])
    // Подписи пульта в строках интерфейса. Слово целиком: «допуск» не «пуск».
    for (const m of js.matchAll(
      /(?<![а-яё])(пуск|включить|выключить|отключить|перезапуст|перезагрузить|команд[аыу]|управлени[ея]|задвижк)/gi,
    ))
      слова.push(js.slice(Math.max(0, m.index - 30), m.index + 30))
  }

  const разрешённые = new Set(Object.keys(ПИШУЩИЕ).map((k) => k.replace(/\{[^}]*\}/g, '{}')))
  expect(вызовы.length, 'пишущие вызовы в бандле найдены — регулярка не ослепла').toBeGreaterThan(0)
  expect(
    вызовы.filter((в) => !разрешённые.has(в)),
    'экран шлёт пишущий запрос, которого нет в ПИШУЩИЕ',
  ).toEqual([])
  expect(чужие, 'экран обращается к адресу вне стенда').toEqual([])
  expect(слова, 'на экране есть подпись управления оборудованием').toEqual([])

  test.info().annotations.push({
    type: 'замер',
    description: `бандлов: ${бандлы.length}; пишущих вызовов с экранов: ${вызовы.length} (${[...new Set(вызовы)].join('; ')}); внешних адресов: 0`,
  })
})
