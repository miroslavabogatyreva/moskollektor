import { useEffect, useState } from 'preact/hooks'
import { Router, route } from 'preact-router'
import { Nav } from './Nav'
import { AlertBar } from './components/AlertBar'
import { ROUTES } from './routes'
import { fetchMe, type AuthUser } from './lib/auth'
import { DashboardScreen } from './screens/dashboard'
import { MapScreen } from './screens/map'
import { LogScreen } from './screens/log'
import { OrdersScreen } from './screens/orders'
import { OrderCard } from './screens/orders/OrderCard'
import { ObjectCard } from './components/ObjectCard'
import { ForecastCard } from './components/ForecastCard'
import { LoginScreen } from './screens/login'
import { DirectoryScreen } from './screens/directory'
import { UsersScreen } from './screens/users'
import { SourcesScreen } from './screens/sources'

function NotFound(_props: Record<string, unknown>) {
  return (
    <main class="p-5">
      <p style="color:var(--text-muted)">
        Такой страницы нет. Вернуться на <a href="/dashboard">дашборд</a>.
      </p>
    </main>
  )
}

export function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)
  const [me, setMe] = useState<AuthUser | null>(null)

  useEffect(() => {
    if (window.location.pathname === '/') route('/dashboard', true)
  }, [])

  useEffect(() => {
    const label = ROUTES.find((r) => r.path === currentPath)?.label
    document.title = label ? `${label} — Москоллектор` : 'Москоллектор'
  }, [currentPath])

  // Один раз на загрузку страницы, и не на /login: там Nav не рисуется
  // вовсе, результат было бы некому показать, а сам запрос без сессии
  // получил бы честный 401 — Chromium логирует любой такой ответ в консоль
  // как ошибку независимо от того, что код его штатно обработал (нашли
  // при проверке DevTools против стенда, 24.09.2026).
  useEffect(() => {
    if (window.location.pathname === '/login') return
    fetchMe()
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  return (
    <>
      {currentPath !== '/login' && <Nav currentPath={currentPath} me={me} />}
      {currentPath !== '/login' && me && <AlertBar key={me.login} login={me.login} />}
      <Router onChange={(e) => setCurrentPath(e.url)}>
        <LoginScreen path="/login" />
        <DashboardScreen path="/dashboard" />
        <MapScreen path="/map" />
        <LogScreen path="/log" />
        <OrdersScreen path="/orders" />
        <OrderCard path="/orders/:orderId" />
        <ObjectCard path="/objects/:sectionId" />
        <ForecastCard path="/forecasts/:forecastId" />
        <DirectoryScreen path="/admin/directory" />
        <UsersScreen path="/admin/users" />
        <SourcesScreen path="/admin/sources" />
        <NotFound default />
      </Router>
    </>
  )
}
