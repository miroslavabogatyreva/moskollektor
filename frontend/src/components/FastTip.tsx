import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'

/* Подсказка к меткам схемы — сразу при наведении. Браузер показывает SVG <title>
   примерно через секунду, и задержку не поменять (Слава, 28.09.2026: «оч долго
   приходится ждать»). Поэтому метки несут текст в <desc>: его читает программа
   чтения с экрана, а браузер свою подсказку по нему не рисует. Обёртка ловит
   наведение делегированием — ближайший предок цели с прямым <desc>. */

function descOf(target: EventTarget | null, stop: Element): string | null {
  for (let el = target as Element | null; el && el !== stop; el = el.parentElement) {
    const d = el.querySelector(':scope > desc')
    if (d) return d.textContent
  }
  return null
}

export function FastTip({ children }: { children: ComponentChildren }) {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; right: boolean } | null>(
    null,
  )
  return (
    <div
      class="relative"
      onPointerMove={(e) => {
        const box = e.currentTarget as HTMLElement
        const text = descOf(e.target, box)
        if (!text) return setTip(null)
        const r = box.getBoundingClientRect()
        const x = e.clientX - r.left
        // В правой половине подсказка растёт влево — не вылезает за край карточки.
        setTip({
          text,
          x: x > r.width / 2 ? r.width - x : x,
          y: e.clientY - r.top,
          right: x > r.width / 2,
        })
      }}
      onPointerLeave={() => setTip(null)}
    >
      {children}
      {tip && (
        <div
          role="tooltip"
          class="absolute z-20 text-xs rounded-md px-2 py-1 shadow-md"
          style={`${tip.right ? 'right' : 'left'}:${tip.x + 8}px; top:${tip.y + 16}px; max-width:320px; pointer-events:none; background:var(--text-primary); color:var(--bg-surface)`}
        >
          {tip.text}
        </div>
      )}
    </div>
  )
}
