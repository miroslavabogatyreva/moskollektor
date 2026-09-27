// Цвет значка по риску и правило плотности (MOS-106, задача 5.22, приёмка М-05).
// Отдельный файл — как viewport.ts/viewport.selfcheck.ts: чистая логика без DOM,
// проверяется node-скриптом, не браузером.

export type RiskClass = 'high' | 'normal' | null | undefined

export interface RiskColors {
  fill: string
  text: string
  border: string
}

// Название состояния — одно на всё приложение (MOS-170): легенда на /map, легенда
// на дашборде (fe, теми же словами) и всплывающая подсказка над значком читают
// его отсюда, а не держат свою копию словами.
export function riskLabel(cls: RiskClass): string {
  if (cls === 'high') return 'высокий риск'
  if (cls === 'normal') return 'низкий риск'
  return 'класса нет'
}

// Сервер считает риск бинарно (backend/app/worker/publish.py:77, класс_риска):
// только "high" и "normal". --risk-medium и --risk-nodata лежат в tokens.css
// до решения о трёхуровневой шкале — здесь им сознательно нет ветки.
//
// «Класса нет» красим не серым из шкалы (--risk-nodata), а нейтралью рамки
// значка (--bg-table/--border-subtle, MOS-106): --risk-nodata у заказчика занят
// под «нет связи с каналом» (dashboard/color-palette.md, разд. 5.1) — это другое
// состояние, путать нельзя.
export function riskColors(cls: RiskClass): RiskColors {
  if (cls === 'high') {
    return {
      fill: 'var(--risk-critical)',
      text: 'var(--risk-critical-text)',
      border: 'var(--risk-critical-border)',
    }
  }
  if (cls === 'normal') {
    return {
      fill: 'var(--risk-low)',
      text: 'var(--risk-low-text)',
      border: 'var(--risk-low-border)',
    }
  }
  return {
    fill: 'var(--bg-table)',
    text: 'var(--text-primary)',
    border: 'var(--border-subtle)',
  }
}

// Форма значка по классу (MOS-173, план 5.30): цвет нигде не работает один —
// dashboard/color-palette.md, разд. 5.1 и 8.4. high — закрашенный треугольник
// («критический» палитры), normal — пустой круг («низкий»). «Класса нет» —
// вертикальная черта, «значения нет»: пунктир и штриховка в палитре заняты
// под «нет связи с каналом» (разд. 5.3), а цветом это состояние MOS-170 от него
// уже увёл. Пустой квадрат не годится: при 6 px он совпадает с пустым кругом.
export type RiskShape = 'triangle' | 'circle' | 'dash'
export function riskShape(cls: RiskClass): RiskShape {
  if (cls === 'high') return 'triangle'
  if (cls === 'normal') return 'circle'
  return 'dash'
}

// Значок 20×20 плюс зазор — 24 единицы SVG на участок. Считаем по видимому окну,
// не по всему коллектору: после зума соседей на экране меньше, и густота обязана
// упасть вместе с ними, иначе приближение (MOS-126) не расчищает номера никогда.
// Проверено живым зумом на коллекторе с тегом 914 (MOS-106, тикет; до MOS-181 —
// номер в выпадающем списке, сейчас участок группы «объект Мю»): 455 участков
// полной осью — густо, чипы без номеров; после пяти шагов «+ приблизить» окно
// ПК242–ПК258, 15 участков — номера вернулись. При проверке браузером жать кнопку
// зума по одному клику с паузой на перерисовку: Preact батчит состояние, и пачка
// кликов без паузы между ними даёт один эффективный шаг вместо пяти.
//
// Меряем расстояние между соседями, а не число меток (MOS-215): у линии 798
// объекта Зита 21 метка на 920 единиц — по числу помещаются, но ПК55/56/57 стоят
// через 13 единиц, и рамки 20×20 лежали друг на друге. Одна тесная пара — вся линия
// в чипы: номера вернёт зум, как у 914.
const MARKER_STEP = 24
export function isDense(xs: number[]): boolean {
  const sorted = [...xs].sort((a, b) => a - b)
  return sorted.some((x, i) => i > 0 && x - sorted[i - 1] < MARKER_STEP)
}
