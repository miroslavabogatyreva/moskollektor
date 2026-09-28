// Спецификации и playwright.config.ts читают из Node только process.env.
// Ради одной строки @types/node не ставим: тип объявлен здесь.
declare const process: { env: Record<string, string | undefined> }

// Мок экранов по датчикам (helpers/sensor-mock.ts) читает фикстуру и справочник участков с диска.
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string
}
