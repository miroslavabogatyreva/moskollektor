import { useEffect, useState } from 'preact/hooks'
import { apiFetch } from '../lib/api'

/* Полоса уведомлений — план 5.6 (MOS-53), приёмка Ф-88, Ф-90.

   Стартовая картина — GET /api/notifications?acked=false, новое — поток
   GET /api/alerts/stream (SSE, id события = id уведомления). Показываем одну
   запись с наибольшим id: у событий проигрывания СМВУ reported_at майский,
   и первым в ответе (reported_at DESC) стоит не самое новое событие.

   «Принял» НЕ квитирует (решение 27.09.2026, план 6.9 и US-04 сц. 5): гаснет
   полоса, а событие остаётся во вкладке «Неквитированные» на экране заявок,
   пока его не отработают кнопкой «Квитировать». Кнопка запоминает в браузере
   id, до которого диспетчер уже видел, — полосу зажжёт только запись новее. */

interface Alert {
  id: number
  object_name: string | null
  smvu_key: string | null
  section_id: number | null
  probability: number
  horizon_h: number
}

// Отметка своя у каждого логина: второй диспетчер за тем же компьютером
// должен увидеть то, что погасил первый.
const seenKey = (login: string) => `alertbar-seen:${login}`

function видел(login: string): number {
  try {
    return Number(localStorage.getItem(seenKey(login))) || 0
  } catch {
    return 0
  }
}

export function AlertBar({ login }: { login: string }) {
  const [alert, setAlert] = useState<Alert | null>(null)

  useEffect(() => {
    const показать = (a: Alert) => {
      if (a.id > видел(login)) setAlert((prev) => (prev && prev.id >= a.id ? prev : a))
    }
    let es: EventSource | undefined
    let отменено = false
    // ponytail: 1000 записей за раз — потолок метода; на стенде 347 (27.09.2026).
    // Упрёмся — нужен параметр сортировки по id у GET /api/notifications.
    apiFetch('/api/notifications?acked=false&limit=1000')
      .then((r) => (r.ok ? (r.json() as Promise<{ items: Alert[] }>) : null))
      .then((body) => {
        // Нет права notifications.read — поток не открываем: EventSource
        // переподключался бы к 403 каждые 3 с до закрытия вкладки.
        if (!body || отменено) return
        body.items.forEach(показать)
        // ponytail: событие, записанное между ответом списка и открытием потока,
        // теряется — окно в миллисекунды (поток без Last-Event-ID стартует
        // с max id в момент подключения). Чинится передачей наибольшего id
        // списка потоку как курсора.
        es = new EventSource('/api/alerts/stream')
        es.onmessage = (e) => показать(JSON.parse(e.data) as Alert)
      })
      .catch(() => {})
    return () => {
      отменено = true
      es?.close()
    }
  }, [login])

  if (!alert) return null

  function принял() {
    try {
      localStorage.setItem(seenKey(login), String(alert!.id))
    } catch {
      /* без хранилища полоса погаснет до перезагрузки — хуже, но работает */
    }
    setAlert(null)
  }

  const место = alert.object_name ?? alert.smvu_key ?? `уведомление ${alert.id}`
  return (
    <div
      role="status"
      aria-label="Уведомление о прогнозе"
      class="flex flex-wrap items-center gap-3 px-5 py-2 text-sm"
      style="background:var(--risk-critical); color:var(--risk-critical-text); border-bottom:1px solid var(--risk-critical-border)"
    >
      <b>Критический прогноз:</b>
      {alert.section_id != null ? (
        <a href={`/objects/${alert.section_id}`} style="color:inherit; text-decoration:underline">
          {место}
        </a>
      ) : (
        <span>{место}</span>
      )}
      <span class="num">вероятность {Math.round(alert.probability * 100)} %</span>
      <span class="num">горизонт {alert.horizon_h} ч</span>
      <a href="/orders" class="ml-auto" style="color:inherit">
        все неквитированные
      </a>
      <button
        type="button"
        onClick={принял}
        class="px-2 py-1 rounded-sm"
        style="background:transparent; border:1px solid currentColor; color:inherit"
      >
        Принял
      </button>
    </div>
  )
}
