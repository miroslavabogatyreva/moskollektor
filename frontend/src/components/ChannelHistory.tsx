import { useEffect, useRef, useState } from 'preact/hooks'
import { apiFetch } from '../lib/api'
import { errorMessage, formatDateTime } from '../lib/format'

/* История отказов одного канала (US-22 сц. 3): техник открывает участок из заявки
   (/objects/:id?channel=:cid) и видит, когда канал терял связь раньше и сколько лежал.
   GET /api/objects/{id}/channels/{cid}/episodes — те же эпизоды, что считает столбец
   «Отказов» таблицы «Отказы по каналам», поэтому строк здесь ровно столько же. */

interface Эпизод {
  started_at: string
  ended_at: string | null
  duration_h: number | null
  fault_value: string
}

interface Ответ {
  channel: { channel_id: number; name: string; sensor_kind: string | null }
  total: number
  items: Эпизод[]
}

export function ChannelHistory({ sectionId, channelId }: { sectionId: string; channelId: string }) {
  const [data, setData] = useState<Ответ | null>(null)
  const [error, setError] = useState<string | null>(null)
  const блок = useRef<HTMLElement>(null)

  // Ссылка на канал стоит и в таблице «Отказы по каналам» внизу карточки: без прокрутки
  // история появлялась вне экрана и клик выглядел пустым (28.09.2026).
  useEffect(() => {
    блок.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [channelId])

  useEffect(() => {
    let отменено = false
    setData(null)
    setError(null)
    apiFetch(`/api/objects/${sectionId}/channels/${channelId}/episodes`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Ответ>
      })
      .then((d) => {
        if (!отменено) setData(d)
      })
      .catch((e) => {
        if (!отменено) setError(errorMessage(e))
      })
    return () => {
      отменено = true
    }
  }, [sectionId, channelId])

  return (
    <section
      ref={блок}
      class="card text-sm"
      style="border-left:4px solid var(--accent); scroll-margin-top:16px"
    >
      <h2 class="font-semibold mb-1">
        История канала{data && ` «${data.channel.name.trim()}» · ${data.channel.sensor_kind ?? ''}`}
      </h2>
      {error && <p style="color:var(--state-error)">Не удалось загрузить историю: {error}</p>}
      {!data && !error && <p style="color:var(--text-muted)">Загрузка…</p>}
      {data && data.total === 0 && <p style="color:var(--text-muted)">Канал связь не терял.</p>}
      {data && data.total > 0 && (
        <>
          <p style="color:var(--text-secondary)">
            Потерь связи: <span class="num">{data.total}</span>, свежие сверху.
          </p>
          <ul style="list-style:none; padding:0; margin:0">
            {data.items.map((e) => (
              <li key={e.started_at} data-episode-start={e.started_at} class="num">
                с {formatDateTime(e.started_at)}
                {e.ended_at
                  ? ` по ${formatDateTime(e.ended_at)} · ${(e.duration_h ?? 0).toFixed(1).replace('.', ',')} ч`
                  : ' · не закрыт'}{' '}
                <span style="color:var(--text-muted)">· {e.fault_value}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
