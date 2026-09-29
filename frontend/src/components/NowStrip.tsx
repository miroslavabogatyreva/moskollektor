import { useEffect, useState } from 'preact/hooks'
import { fetchMe } from '../lib/auth'
import { formatTime, МОСКВА } from '../lib/format'
import { коллекторов } from '../lib/plural'
import { usePoll } from '../lib/poll'
import { fetchWeatherNow } from '../screens/dashboard/api'
import type { WeatherNow } from '../screens/dashboard/types'

// Зона видимости словами роли (Ф-94): ОДС и администратор видят весь парк,
// диспетчер — свой район, техник — свой комплекс. Имени района API не отдаёт,
// поэтому зону меряем числом коллекторов, которые пришли в ответе рисков.
function зона(roles: string[]): string {
  if (roles.includes('ods_dispatcher') || roles.includes('admin')) return 'весь парк'
  if (roles.includes('dispatcher')) return 'ваш район'
  if (roles.includes('technician')) return 'ваш комплекс'
  return 'ваша зона'
}

const день = new Intl.DateTimeFormat('ru-RU', {
  timeZone: МОСКВА,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

/* Полоса «сейчас»: зона, дата и часы по Москве, погода за окном. Стоит на
   всех пяти вкладках меню (Слава, 29.09.2026), поэтому погоду и роль грузит сама;
   погода — с общим опросом раз в минуту. Часы тикают раз в 15 секунд —
   минуты на них не должны отставать от настенных. */
export function NowStrip({ коллекторов: n = null }: { коллекторов?: number | null }) {
  const [сейчас, setСейчас] = useState(() => new Date())
  const [weather, setWeather] = useState<WeatherNow | 'нет' | null>(null)
  const [roles, setRoles] = useState<string[] | null>(null)
  const { tick } = usePoll()
  useEffect(() => {
    const id = setInterval(() => setСейчас(new Date()), 15_000)
    fetchMe()
      .then((u) => setRoles(u?.roles ?? []))
      .catch(() => setRoles([]))
    return () => clearInterval(id)
  }, [])
  useEffect(() => {
    let отменено = false
    fetchWeatherNow()
      .then((w) => !отменено && setWeather(w))
      .catch(() => !отменено && setWeather('нет'))
    return () => {
      отменено = true
    }
  }, [tick])
  return (
    <div data-testid="now-strip" class="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-sm">
      <span>
        <b>Москва</b>
        {roles && (
          <span style="color:var(--text-secondary)">
            {' '}
            · {зона(roles)}
            {n != null && `, ${коллекторов(n)}`}
          </span>
        )}
      </span>
      <span>
        {день.format(сейчас).replace(' г.', '')},{' '}
        <b class="num">{formatTime(сейчас).slice(0, 5)}</b>
      </span>
      <span
        title={
          weather && weather !== 'нет'
            ? `${weather.source}, на ${weather.observed_at.slice(11)}`
            : undefined
        }
      >
        {weather === null ? (
          <span style="color:var(--text-muted)">погода…</span>
        ) : weather === 'нет' ? (
          <span style="color:var(--text-muted)">погода недоступна</span>
        ) : (
          <>
            <b class="num">
              {weather.temp_c > 0 ? '+' : ''}
              {Math.round(weather.temp_c)} °C
            </b>{' '}
            <span style="color:var(--text-secondary)">
              · {weather.sky} · ветер {Math.round(weather.wind_ms)} м/с
              {weather.precip_mm > 0 && ` · осадки ${weather.precip_mm} мм`}
            </span>
          </>
        )}
      </span>
    </div>
  )
}
