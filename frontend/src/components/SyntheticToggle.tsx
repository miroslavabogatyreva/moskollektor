import { route } from 'preact-router'
import { сПараметром } from '../lib/sensorRisk'

// Переключатель «Учитывать паспорт оборудования (синтетика)» — один на дашборд
// и схему (эпик MOS-248, SL.5 и SL.6). Состояние живёт в адресе: выключенный —
// ?synthetic=0, включённый — без параметра. Переход с заменой записи истории:
// «Назад» уводит с экрана, а не щёлкает переключатель обратно.
// Плашка «Демо» видна, пока синтетика включена: на экране есть выдуманные
// паспорта, и это должно читаться без наведения мыши.
export function SyntheticToggle({ on }: { on: boolean }) {
  const toggle = () => {
    const { pathname, search } = window.location
    route(сПараметром(pathname, search, 'synthetic', on ? '0' : null), true)
  }
  return (
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      <label class="flex items-center gap-2 cursor-pointer" style="color:var(--text-primary)">
        <input
          type="checkbox"
          data-testid="synthetic-toggle"
          checked={on}
          onChange={toggle}
          class="w-4 h-4"
          style="accent-color:var(--brand)"
        />
        Учитывать паспорт оборудования (синтетика)
      </label>
      {on && (
        <p
          role="note"
          data-testid="synthetic-note"
          class="rounded px-3 py-1"
          style="background:var(--risk-medium); color:var(--risk-medium-text); border:1px solid var(--risk-medium-border)"
        >
          <strong>Демо: паспорта синтетические.</strong> Номера, даты ввода и поверки выдуманы,
          история отказов реальная.
        </p>
      )}
    </div>
  )
}
