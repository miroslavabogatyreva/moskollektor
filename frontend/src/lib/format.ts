// Дата и время в одном формате на весь фронт: "14.09.2026 09:05" (docs/acceptance-test.md,
// М-12) — без запятой и без секунд, в отличие от toLocaleString('ru-RU') по умолчанию.
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
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
