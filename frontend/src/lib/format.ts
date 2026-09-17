// Дата и время в одном формате на весь фронт: "14.09.2026 09:05" (docs/acceptance-test.md,
// М-12) — без запятой и без секунд, в отличие от toLocaleString('ru-RU') по умолчанию.
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
