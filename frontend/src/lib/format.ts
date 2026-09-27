// Все даты на экране — по Москве, а не в поясе браузера (MOS-121): getHours() и
// toLocaleString() без timeZone берут пояс машины, и комиссия с ноутбука во
// Владивостоке увидела бы срез данных, расчёт и срок заявки на 7 часов позже.
// Москва держит UTC+3 круглый год с 2014-го, поэтому смещение в полях ввода
// (audit, log) можно писать числом.
export const МОСКВА = 'Europe/Moscow'
export const МОСКВА_СМЕЩЕНИЕ = '+03:00'

const части = new Intl.DateTimeFormat('ru-RU', {
  timeZone: МОСКВА,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

function поля(value: string | Date): Record<string, string> {
  const итог: Record<string, string> = {}
  for (const p of части.formatToParts(new Date(value))) итог[p.type] = p.value
  return итог
}

// Дата и время в одном формате на весь фронт: "14.09.2026 09:05" (docs/acceptance-test.md,
// М-12) — без запятой и без секунд. Секунды — только там, где они различают
// записи: журнал действий пользователей, прогнозы одного расчёта.
export function formatDateTime(value: string | Date, секунды = false): string {
  const п = поля(value)
  const время = секунды ? `${п.hour}:${п.minute}:${п.second}` : `${п.hour}:${п.minute}`
  return `${п.day}.${п.month}.${п.year} ${время}`
}

// "14.09.2026" — московская дата момента.
export function formatDate(value: string | Date): string {
  const п = поля(value)
  return `${п.day}.${п.month}.${п.year}`
}

// "09:05:17" — московское время момента.
export function formatTime(value: string | Date): string {
  const п = поля(value)
  return `${п.hour}:${п.minute}:${п.second}`
}

// "2026-09-14" — московская дата для <input type="date"> и параметров API.
export function isoDateMoscow(value: string | Date): string {
  return new Date(value).toLocaleDateString('sv-SE', { timeZone: МОСКВА })
}

// String(new Error('401 Unauthorized')) даёт "Error: 401 Unauthorized" — лишнее
// слово перед текстом, который и так понятен диспетчеру. .catch у нас всегда
// ловит либо Error (из throw new Error(...)), либо что-то нетипичное — тогда
// показываем как есть.
export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// Имя участка — одно на все экраны (US-14, НФ-71, MOS-243). Правило сервера
// слово в слово: asset.func_location.name = 'Коллектор ' || префикс smvu_key ||
// ', пикет ' || пикет (db/migrations/010_orders.sql:155), его отдают заявки полем
// object_name. Собираем из smvu_key, а не из collector файла sections.json: после
// MOS-181 collector — номер коллектора дерева заказчика (16 значений), не префикс
// тега, и дашборд звал участок 2300 «Коллектор 15» вместо «Коллектор 884».
export function имяУчастка(smvuKey: string): string {
  const [коллектор, пикет] = smvuKey.split(':')
  return `Коллектор ${коллектор}, пикет ${пикет}`
}
