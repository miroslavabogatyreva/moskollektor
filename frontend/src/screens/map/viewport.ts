// Масштаб оси пикетов (MOS-126, задача 5.15). Чистая арифметика окна просмотра,
// без DOM и без Preact — идёт в бандл карты, поэтому здесь нет ничего из Node
// (process и т.п.). Самопроверка — в соседнем viewport.selfcheck.ts, который
// в бандл не попадает, потому что экран его не импортирует.

export type ViewRange = [number, number]

export function fullView(maxPicket: number): ViewRange {
  return [0, maxPicket]
}

export function isFullView(view: ViewRange, maxPicket: number): boolean {
  return view[0] <= 0 && view[1] >= maxPicket
}

function clamp(view: ViewRange, maxPicket: number): ViewRange {
  let [start, end] = view
  const width = end - start
  if (start < 0) {
    start = 0
    end = width
  }
  if (end > maxPicket) {
    end = maxPicket
    start = end - width
  }
  start = Math.max(0, start) // страховка от -0 и от округления с плавающей точкой (порядка -1e-12), не от выхода за край
  return [start, end]
}

// factor < 1 сужает окно (приближение), factor > 1 расширяет (отдаление).
// center — пикет, вокруг которого масштабируем: курсор при колесе, середина
// текущего окна при клике по кнопке.
export function zoomView(
  view: ViewRange,
  factor: number,
  center: number,
  maxPicket: number,
  minWidth: number,
): ViewRange {
  const [start, end] = view
  const width = end - start
  // Вырожденная ось (width = 0, например maxPicket = 0) иначе делит 0/0 в ratio
  // ниже, и NaN расползается по всему окну просмотра — нашла 28, MOS-126.
  if (width <= 0) return fullView(maxPicket)
  const newWidth = Math.min(maxPicket, Math.max(minWidth, width * factor))
  const ratio = newWidth / width
  const newStart = center - (center - start) * ratio
  return clamp([newStart, newStart + newWidth], maxPicket)
}

// deltaFraction — доля текущей ширины окна, на которую сдвигаем ось вбок;
// отрицательное значение — влево, положительное — вправо.
export function panView(view: ViewRange, deltaFraction: number, maxPicket: number): ViewRange {
  const [start, end] = view
  const delta = (end - start) * deltaFraction
  return clamp([start + delta, end + delta], maxPicket)
}
