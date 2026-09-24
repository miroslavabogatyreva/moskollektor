// Вход по логину и паролю — MOS-39 (Q4.2), решение Славы 24.09.2026. US-24
// (docs/user-stories.md) описывает сценарии LDAP-семантики (роль из группы,
// блокировка в каталоге) — их проверяет бригада на своём каталоге (НФ-76,
// задания A и B). Здесь — общая механика формы входа поверх ДОГОВОРА API,
// которую задание C называет отдельно: разница ролей, неверный пароль, выход,
// прямой переход без сессии, ответ «Проверить соединение» (Ф-66, НФ-43, НФ-85).
//
// Пароли демо-учёток тест не хранит — берёт у GET /api/auth/info тем же
// способом, что и подсказка на экране (helpers/auth.ts).
import { expect, test } from '@playwright/test'
import { account, demoAccounts, loginAs, logout } from './helpers/auth'

// Реальный вход кукой, а не заголовком: иначе бы AUTH_TRUST_HEADER=1 на стенде
// пускал мимо формы, и тест не отличил бы работающий вход от неработающего.
test.use({ extraHTTPHeaders: {} })

test('диспетчер ОДС видит весь парк, техник — меньше объектов (Ф-66, разные роли)', async ({
  page,
}) => {
  const accounts = await demoAccounts(page)
  const tech = account(accounts, 'technician')
  const ods = account(accounts, 'ods_dispatcher')
  const tile = page.locator('article', { hasText: 'Участков в расчёте' }).locator('.num')

  await loginAs(page, tech.login, tech.password)
  const techTotal = Number(await tile.innerText())
  await logout(page)

  await loginAs(page, ods.login, ods.password)
  const odsTotal = Number(await tile.innerText())

  expect(techTotal).toBeGreaterThan(0)
  expect(techTotal).toBeLessThan(odsTotal)
})

test('«Служба каталогов» видна и доступна только администратору (НФ-43)', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const admin = account(accounts, 'admin')
  const dispatcher = account(accounts, 'dispatcher')
  const menu = page.getByRole('navigation', { name: 'Разделы' })

  await loginAs(page, dispatcher.login, dispatcher.password)
  await expect(menu.getByRole('link', { name: 'Служба каталогов' })).toHaveCount(0)
  // НФ-43: скрытый пункт меню — не единственная защита, прямой переход
  // по адресу тоже обязан отказать, а не показать содержимое.
  await page.goto('/admin/directory')
  await expect(page.getByText('Доступ запрещён')).toBeVisible()
  await logout(page)

  await loginAs(page, admin.login, admin.password)
  await menu.getByRole('link', { name: 'Служба каталогов' }).click()
  await page.waitForURL('**/admin/directory')
  await expect(page.getByText('Доступ запрещён')).toHaveCount(0)
})

test('у администратора кнопка «Проверить соединение» даёт ответ (НФ-76)', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const admin = account(accounts, 'admin')
  await loginAs(page, admin.login, admin.password)
  await page.goto('/admin/directory')
  await page.getByRole('button', { name: 'Проверить соединение' }).click()
  await expect(page.getByText(/мс/)).toBeVisible()
})

test('неверный пароль показывает ошибку по-русски и не пускает', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const someone = accounts[0]
  await page.goto('/login')
  await page.getByLabel('Логин').fill(someone.login)
  await page.getByLabel('Пароль').fill(`${someone.password}-неверный`)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.locator('form p')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('выход возвращает на /login и закрывает сессию', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const someone = accounts[0]
  await loginAs(page, someone.login, someone.password)
  await logout(page)
  // Сессия закрыта не только на экране: прямой переход снова уводит на /login.
  await page.goto('/dashboard')
  await page.waitForURL('**/login**')
})

test('прямой переход на экран без сессии уводит на /login и возвращает после входа', async ({
  page,
}) => {
  const accounts = await demoAccounts(page)
  const someone = accounts[0]

  await page.goto('/orders')
  await page.waitForURL('**/login**')
  expect(new URL(page.url()).searchParams.get('next')).toBe('/orders')

  await page.getByLabel('Логин').fill(someone.login)
  await page.getByLabel('Пароль').fill(someone.password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await page.waitForURL('**/orders')
})
