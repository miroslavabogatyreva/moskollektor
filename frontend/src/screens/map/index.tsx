/* Ось пикетов — задача 5.3. Заголовок экрана отличается от подписи в меню:
   меню называет назначение раздела (М-05, "Карта объектов"), заголовок — способ
   показа внутри него. */
export function MapScreen(_props: Record<string, unknown>) {
  return (
    <main class="p-5">
      <h1 style="font-family:var(--font-display)" class="text-lg font-semibold">
        Схема коллектора по пикетам
      </h1>
    </main>
  )
}
