import type { Page } from '@playwright/test'

// ДОГОВОР API MOS-39, GET /api/auth/info. Пароли демо-учёток задаёт
// db/seed/rbac.sql (задание бригады A) — тест не хранит их у себя, а читает
// у сервера тем же способом, что и таблица подсказки на экране входа: разошлись
// бы пароль в сиде и пароль в тесте — эта функция достала бы актуальный.
export interface DemoAccount {
  login: string
  password: string
  role_code: string
  role_name: string
  sees: string
}

export async function demoAccounts(page: Page): Promise<DemoAccount[]> {
  const r = await page.request.get('/api/auth/info')
  if (!r.ok()) throw new Error(`GET /api/auth/info: ${r.status()}`)
  const body = (await r.json()) as { demo_accounts: DemoAccount[] }
  if (body.demo_accounts.length === 0) {
    throw new Error('demo_accounts пуст — на стенде должен стоять AUTH_DEMO_HINTS=1')
  }
  return body.demo_accounts
}

export function account(accounts: DemoAccount[], roleCode: string): DemoAccount {
  const a = accounts.find((x) => x.role_code === roleCode)
  if (!a) throw new Error(`демо-учётка с ролью ${roleCode} не найдена в GET /api/auth/info`)
  return a
}

export async function loginAs(page: Page, login: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('Логин').fill(login)
  await page.getByLabel('Пароль').fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await page.waitForURL('**/dashboard')
}

export async function logout(page: Page) {
  await page.getByRole('button', { name: 'Выйти' }).click()
  await page.waitForURL('**/login')
}
