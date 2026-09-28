// US-24. Вход по учётной записи каталога — docs/user-stories.md, приёмка НФ-76, Ф-66.
// Каталог — демо-сервер slapd в контейнере ldap (deploy/ldap/, MOS-39): свой AD
// заказчик не даёт, «Любой LDAP сервис» (ответ 31). Учётки каталога — ldap_ods1,
// ldap_tech1 и другие из deploy/ldap/bootstrap.ldif, пароль у всех один и открыт
// в deploy/README.md, раздел «Поднять каталог LDAP»: это демо, а не секрет.
//
// Сц. 2 из облака не проверить: блокирует учётку скрипт на сервере
// (deploy/ldap/block-user.sh), наружу порт каталога не открыт. Поэтому сц. 2 идёт,
// только когда E2E_LDAP_BLOCKED называет заблокированную учётку, — порядок
// прогона написан в docs/user-stories.md, «Результаты прогона», US-24.
import { expect, test, type Page } from '@playwright/test'
import { account, demoAccounts, loginAs, logout } from './helpers/auth'

const ПАРОЛЬ_КАТАЛОГА = process.env.E2E_LDAP_PASSWORD ?? 'LdapDemo#1'
const ЗАБЛОКИРОВАН = process.env.E2E_LDAP_BLOCKED

interface AppUser {
  login: string
  auth_source: string
  is_active: boolean
  has_password: boolean
  roles: string[]
}

// Вход кукой через форму, без заголовка X-User-Login: иначе AUTH_TRUST_HEADER=1
// на стенде пустил бы мимо каталога, и тест не отличил бы вход через LDAP от доверия.
test.use({ extraHTTPHeaders: {} })

// После входа открывается главная (/map); плитка — на дашборде «по участкам».
const участков = (page: Page) => ({
  innerText: async () => {
    await page.goto('/dashboard?view=sections')
    return page.locator('article', { hasText: 'Участков в расчёте' }).locator('.num').innerText()
  },
})

async function пользователи(page: Page): Promise<AppUser[]> {
  const r = await page.request.get('/api/auth/users')
  expect(r.status(), 'GET /api/auth/users под администратором').toBe(200)
  return (await r.json()) as AppUser[]
}

async function войтиАдминистратором(page: Page) {
  const admin = account(await demoAccounts(page), 'admin')
  await loginAs(page, admin.login, admin.password)
}

test('US-24 сц. 1: роль берётся из группы каталога', async ({ page }) => {
  const ods = account(await demoAccounts(page), 'ods_dispatcher')
  // Весь парк — столько участков видит локальный диспетчер ОДС.
  await loginAs(page, ods.login, ods.password)
  const весьПарк = Number(await участков(page).innerText())
  await logout(page)

  await loginAs(page, 'ldap_ods1', ПАРОЛЬ_КАТАЛОГА)
  const пользователь = (await (await page.request.get('/api/auth/me')).json()) as {
    roles: string[]
    auth_source: string
  }
  expect(пользователь.auth_source, 'вход прошёл через каталог').toBe('ldap')
  expect(пользователь.roles, 'роль из группы role-ods').toEqual(['ods_dispatcher'])
  await expect(page.getByText('диспетчер ОДС').first()).toBeVisible()
  expect(Number(await участков(page).innerText()), 'весь парк').toBe(весьПарк)
  await logout(page)

  // Другая группа — другая роль и меньше объектов: роль правда берётся из группы.
  await loginAs(page, 'ldap_tech1', ПАРОЛЬ_КАТАЛОГА)
  const уТехника = Number(await участков(page).innerText())
  expect(уТехника).toBeGreaterThan(0)
  expect(уТехника).toBeLessThan(весьПарк)
})

test('US-24 сц. 2: блокировка в каталоге закрывает вход', async ({ page }) => {
  test.skip(
    !ЗАБЛОКИРОВАН,
    'нужна блокировка на сервере: sh deploy/ldap/block-user.sh <login>, затем E2E_LDAP_BLOCKED=<login>',
  )
  await page.goto('/login')
  await page.getByLabel('Логин').fill(ЗАБЛОКИРОВАН!)
  await page.getByLabel('Пароль').fill(ПАРОЛЬ_КАТАЛОГА)
  const ответ = page.waitForResponse((r) => r.url().endsWith('/api/auth/login'))
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  expect((await ответ).status(), 'вход не прошёл').toBe(401)
  await expect(page.locator('form p')).toHaveText(/[а-яё]/i)
  await expect(page).toHaveURL(/\/login$/)

  // Отказал каталог, а не сервис: каталог на связи, а в сервисе учётка активна —
  // её не блокировали кнопкой на экране «Пользователи».
  await войтиАдминистратором(page)
  const связь = await page.request.post('/api/auth/directory/check')
  expect(((await связь.json()) as { ok: boolean }).ok, 'каталог на связи').toBe(true)
  const учётка = (await пользователи(page)).find((u) => u.login === ЗАБЛОКИРОВАН)
  expect(учётка?.is_active, 'в сервисе учётка не заблокирована').toBe(true)
})

test('US-24 сц. 3: пароля пользователя каталога в сервисе нет', async ({ page }) => {
  // Дано: пользователь хотя бы раз входил.
  await loginAs(page, 'ldap_ods1', ПАРОЛЬ_КАТАЛОГА)
  await logout(page)

  await войтиАдминистратором(page)
  const учётка = (await пользователи(page)).find((u) => u.login === 'ldap_ods1')
  expect(учётка?.auth_source).toBe('ldap')
  expect(учётка?.has_password, 'хеша пароля в базе нет').toBe(false)
  // Ответ API не несёт ни пароля, ни хеша ни у кого — только признак.
  for (const u of await пользователи(page))
    expect(Object.keys(u).filter((k) => /pass|hash/i.test(k))).toEqual(['has_password'])

  await page.goto('/admin/users')
  const строка = page.locator('main table tbody tr', { hasText: 'ldap_ods1' })
  await expect(строка.getByTestId('password-cell')).toHaveText('нет, проверяет каталог')
})

test('US-24 сц. 4: ровно четыре роли словами заказчика', async ({ page }) => {
  await войтиАдминистратором(page)
  await page.goto('/admin/directory')
  await expect(page.locator('main table tbody tr').first()).toBeVisible()
  const роли = await page.locator('main table tbody td', { hasText: /^роль: / }).allInnerTexts()
  expect(new Set(роли.map((т) => т.replace(/^роль: /, '').trim()))).toEqual(
    new Set(['диспетчер', 'диспетчер ОДС', 'техник', 'администратор ИС']),
  )
})
