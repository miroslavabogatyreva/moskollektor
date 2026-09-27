// E2E по пользовательским историям: один файл e2e/us-NN-*.spec.ts на историю
// из docs/user-stories.md, один test() на сценарий. Порядок работы — скилл story-tdd.
//
// Гоняем против живого стенда, а не мока: мок отвечает по контракту, стенд — по факту.
// Стенд открываем по имени: голый IP облачный прокси не пропускает (CONNECT 403).
// ignoreHTTPSErrors оставлен ради облачной сессии: её прокси подменяет сертификат
// стенда своим, и Chromium отвечает ERR_CERT_AUTHORITY_INVALID. С Мака сертификат
// Let's Encrypt настоящий, флаг там ничего не меняет.
//
// E2E_CHROMIUM — путь к своему Chromium, когда в системе стоит не та сборка, что ждёт
// Playwright. В облачной сессии это /opt/pw-browsers/chromium: там сборка 1194,
// а Playwright 1.63 ищет 1243 и падает на запуске браузера.
import { defineConfig } from '@playwright/test'

const chromium = process.env.E2E_CHROMIUM

export default defineConfig({
  testDir: 'e2e',
  reporter: 'list',
  use: {
    baseURL: process.env.BASE_URL ?? 'https://moskollektor.mbogatyreva.ru',
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { 'X-User-Login': process.env.E2E_LOGIN ?? 'dispatcher1' },
    trace: 'retain-on-failure',
    ...(chromium && { launchOptions: { executablePath: chromium } }),
  },
})
