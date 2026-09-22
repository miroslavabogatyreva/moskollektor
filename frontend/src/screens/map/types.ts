// Вид объекта из дерева заказчика (smvu.object_tree.kind), 5.12 (MOS-122, Ф-93).
// У участка бывает несколько видов сразу — датчик диспетчерского дома и охранной
// зоны на одном пикете, 435 из 3173.
export type ObjectKind = 'controlHouse' | 'guardObject'

export interface Section {
  section_id: number
  smvu_key: string
  // collector — collector_id дерева объектов заказчика (16 значений), не префикс
  // тега из smvu_key (MOS-181, М-05): по нему же считает риск прогноз и отдаёт
  // GET /api/objects/{id}, подпись группы — collector_name, имя из того же дерева.
  collector: number
  // Опционально по тому же поводу, что kinds ниже: во время того же окна
  // рассинхрона старый sections.json несёт старый collector (префикс тега) и
  // вовсе без этого поля — компилятор обязан видеть читателя без него.
  collector_name?: string
  picket: number
  // Опционально: бандл и sections.json выкладываются раздельно (rsync без атомарной
  // подмены), окно рассинхрона реально — стенд отдавал участки без kinds вовсе,
  // пока новый файл данных не доехал (MOS-122, находка ab, разбор оркестратора
  // 22.09.2026). Обязательный тип скрыл бы будущего читателя без ?? [] от компилятора.
  kinds?: ObjectKind[]
}

// Срез GET /api/risks, который нужен схеме — не весь RiskRow дашборда
// (frontend/src/screens/dashboard/types.ts), а только класс для цвета значка.
export interface RiskClassRow {
  section_id: number
  risk_class?: 'high' | 'normal' | null
}
