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
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const r = await fetch(path, { ...init, credentials: 'same-origin' })
  if (r.status === 401 && window.location.pathname !== '/login') {
    const next = window.location.pathname + window.location.search
    window.location.href = `/login?next=${encodeURIComponent(next)}`
  }
  return r
}
