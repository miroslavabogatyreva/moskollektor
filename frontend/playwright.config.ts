// E2E по пользовательским историям: один файл e2e/us-NN-*.spec.ts на историю
// из docs/user-stories.md, один test() на сценарий. Порядок работы — скилл story-tdd.
//
// Гоняем против живого стенда, а не мока: мок отвечает по контракту, стенд — по факту.
// У стенда самоподписанный сертификат, поэтому ignoreHTTPSErrors; прокси, который нужен
// MCP-браузеру, здесь не нужен.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'e2e',
  reporter: 'list',
  use: {
    baseURL: process.env.BASE_URL ?? 'https://135.106.216.101',
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { 'X-User-Login': process.env.E2E_LOGIN ?? 'dispatcher1' },
    trace: 'retain-on-failure',
  },
})
