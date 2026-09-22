import { useEffect, useState } from 'preact/hooks'
import { Router, route } from 'preact-router'
import { Nav } from './Nav'
import { ROUTES } from './routes'
import { DashboardScreen } from './screens/dashboard'
import { MapScreen } from './screens/map'
import { LogScreen } from './screens/log'
import { OrdersScreen } from './screens/orders'
import { OrderCard } from './screens/orders/OrderCard'
import { ObjectCard } from './components/ObjectCard'
import { ForecastCard } from './components/ForecastCard'

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

  useEffect(() => {
    if (window.location.pathname === '/') route('/dashboard', true)
  }, [])

  useEffect(() => {
    const label = ROUTES.find((r) => r.path === currentPath)?.label
    document.title = label ? `${label} — Москоллектор` : 'Москоллектор'
  }, [currentPath])

  return (
    <>
      <Nav currentPath={currentPath} />
      <Router onChange={(e) => setCurrentPath(e.url)}>
        <DashboardScreen path="/dashboard" />
        <MapScreen path="/map" />
        <LogScreen path="/log" />
        <OrdersScreen path="/orders" />
        <OrderCard path="/orders/:orderId" />
        <ObjectCard path="/objects/:sectionId" />
        <ForecastCard path="/forecasts/:forecastId" />
        <NotFound default />
      </Router>
    </>
  )
}
