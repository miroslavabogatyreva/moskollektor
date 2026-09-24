// Пользователи — MOS-226 (Q4.19), решение Славы 24.09.2026: Ф-66 закрываем
// кнопкой в интерфейсе (не заводим учётки — их даёт каталог AD, US-24).
// Номер истории — US-30 «Заблокировать пользователя» (решение 6b 24.09.2026,
// следующий свободный после US-29). Саму историю в docs/user-stories.md
// дописывает 6b при правке документов после «принято» — не моя зона.
//
// Подопытный — tech2 (db/seed/rbac.sql, вне demo_accounts, задание бригады
// MOS-226 п. «ОБЩЕЕ»). Пароль не достать через GET /api/auth/info, как
// остальные демо-учётки: 41 24.09.2026 перегенерировала хеш tech2 на новый
// известный пароль (коммит 95c1de4, ветка feat/mos226-users-api) — тот же
// уровень открытости, что у dispatcher123/tech123456 в DEMO_ACCOUNTS,
// только tech2 из подсказки на экране входа нарочно исключён. E2E_TECH2_PASSWORD
// оставлен как запасной путь, если пароль снова сменится без правки теста.
import { expect, test } from '@playwright/test'
import { account, demoAccounts, loginAs, logout } from './helpers/auth'

const TECH2_PASSWORD = process.env.E2E_TECH2_PASSWORD ?? 'tech2123123'

test.use({ extraHTTPHeaders: {} })

test('пункт «Пользователи» виден только администратору (Ф-66, НФ-43)', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const admin = account(accounts, 'admin')
  const dispatcher = account(accounts, 'dispatcher')
  const menu = page.getByRole('navigation', { name: 'Разделы' })

  await loginAs(page, dispatcher.login, dispatcher.password)
  await expect(menu.getByRole('link', { name: 'Пользователи' })).toHaveCount(0)
  await page.goto('/admin/users')
  await expect(page.getByText('Доступ запрещён')).toBeVisible()
  await logout(page)

  await loginAs(page, admin.login, admin.password)
  await menu.getByRole('link', { name: 'Пользователи' }).click()
  await page.waitForURL('**/admin/users')
  await expect(page.getByText('Доступ запрещён')).toHaveCount(0)
  await expect(page.getByRole('cell', { name: 'tech2', exact: true })).toBeVisible()
})

test('у своей строки нет кнопки блокировки', async ({ page }) => {
  const accounts = await demoAccounts(page)
  const admin = account(accounts, 'admin')
  await loginAs(page, admin.login, admin.password)
  await page.goto('/admin/users')
  const ownRow = page.getByRole('row', { name: new RegExp(admin.login) })
  await expect(ownRow.getByRole('button')).toHaveCount(0)
})

test('администратор блокирует и разблокирует tech2 кнопкой в интерфейсе (Ф-66)', async ({
  page,
}) => {
  const accounts = await demoAccounts(page)
  const admin = account(accounts, 'admin')

  try {
    await loginAs(page, admin.login, admin.password)
    await page.goto('/admin/users')
    const row = page.getByRole('row', { name: /tech2/ })
    await expect(row.getByText('активна')).toBeVisible()
    await row.getByRole('button', { name: 'Заблокировать' }).click()
    await expect(row.getByText('заблокирована')).toBeVisible()

    const blocked = await page.request.newContext()
    const loginResp = await blocked.post('/api/auth/login', {
      data: { login: 'tech2', password: TECH2_PASSWORD },
    })
    expect(loginResp.status()).toBe(401)
    await blocked.dispose()

    await row.getByRole('button', { name: 'Разблокировать' }).click()
    await expect(row.getByText('активна')).toBeVisible()

    const unblocked = await page.request.newContext()
    const loginResp2 = await unblocked.post('/api/auth/login', {
      data: { login: 'tech2', password: TECH2_PASSWORD },
    })
    expect(loginResp2.status()).toBe(200)
    await unblocked.dispose()
  } finally {
    // Уборка: is_active должен остаться true независимо от того, где упал тест.
    await page.request.patch('/api/auth/users/tech2', { data: { is_active: true } })
  }
})
