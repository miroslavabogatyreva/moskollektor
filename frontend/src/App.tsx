import { useEffect, useState } from 'preact/hooks'
import { Router, route } from 'preact-router'
import { Nav } from './Nav'
import { DashboardScreen } from './screens/dashboard'
import { MapScreen } from './screens/map'
import { LogScreen } from './screens/log'

export function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)

  useEffect(() => {
    if (window.location.pathname === '/') route('/dashboard', true)
  }, [])

  return (
    <>
      <Nav currentPath={currentPath} />
      <Router onChange={(e) => setCurrentPath(e.url)}>
        <DashboardScreen path="/dashboard" />
        <MapScreen path="/map" />
        <LogScreen path="/log" />
      </Router>
    </>
  )
}
