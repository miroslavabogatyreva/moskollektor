// Один обёрточный fetch на все запросы к /api/* — задача MOS-39 (Q4.2, вход по
// логину и паролю). Личность едет кукой mk_session (HttpOnly; Secure), не
// заголовком X-User-Login: credentials 'same-origin' — единственное, что нужно
// добавить к каждому запросу, чтобы браузер её отправил.
//
// 401 здесь — это не «эта заявка чужая» (то 403, экраны решают сами, ДОГОВОР
// АПИ), а «сессии нет вовсе»: сервер отменил куку или её не было. Уводим на
// /login с адресом, откуда пришли, — после входа возвращаемся туда же. Если
// уже стоим на /login, знаем это и так, второй раз никуда не уводим (иначе
// собственный запрос экрана входа зациклил бы себя).
//
// redirecting — на странице обычно не один запрос: /dashboard разом шлёт
// fetchRisks и fetchDataStatus, App.tsx рядом спрашивает fetchMe. Без сессии
// все получают 401 почти одновременно, и без флага каждый по очереди
// перезапускал бы переход на /login — второй window.location.href обрывает
// ещё не завершённый первый (нашли на живом стенде, 24.09.2026: Playwright
// ловил net::ERR_ABORTED на этой гонке).
let redirecting = false

// Выкладка пересоздаёт контейнер api, и полминуты nginx отвечает 502 (28.09.2026:
// Слава открыла схему в эту минуту, и дерево, сводка и датчики так и остались
// «не загрузились: 502» — сами экраны второй раз не спрашивают). GET на 502/503/504
// и на обрыв сети повторяем через 2, 4, 8 и 16 с — всего до 30 с; запись (POST и
// прочие) не повторяем: она могла дойти до базы.
const ПАУЗЫ_МС = [2000, 4000, 8000, 16000]
const СЕРВЕР_ПЕРЕЗАПУСКАЕТСЯ = new Set([502, 503, 504])

async function fetchСПовтором(path: string, init: RequestInit): Promise<Response> {
  const чтение = (init.method ?? 'GET').toUpperCase() === 'GET'
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(path, init)
      if (!чтение || i >= ПАУЗЫ_МС.length || !СЕРВЕР_ПЕРЕЗАПУСКАЕТСЯ.has(r.status)) return r
    } catch (e) {
      if (!чтение || i >= ПАУЗЫ_МС.length || init.signal?.aborted) throw e
    }
    await new Promise((ok) => setTimeout(ok, ПАУЗЫ_МС[i]))
    if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError')
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const r = await fetchСПовтором(path, { ...init, credentials: 'same-origin' })
  if (r.status === 401 && !redirecting && window.location.pathname !== '/login') {
    redirecting = true
    const next = window.location.pathname + window.location.search
    window.location.href = `/login?next=${encodeURIComponent(next)}`
  }
  return r
}
