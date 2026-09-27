// Спецификации и playwright.config.ts читают из Node только process.env.
// Ради одной строки @types/node не ставим: тип объявлен здесь.
declare const process: { env: Record<string, string | undefined> }
