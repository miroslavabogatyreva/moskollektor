/* Дерево объектов диспетчера слева от оси — задача 5.9 (MOS-101), приёмка М-05.
   Два яруса из GET /api/objects/tree: коллектор (узел уровня 2 smvu.object_tree,
   16 штук) и узлы под ним (уровень 3, на стенде 67 с каналами на участках).
   Имена узлов — «ДП объект Бета», «Шкаф ОПС объект Хи» — диспетчер знает
   наизусть, section_id ему ничего не говорит. Узлы раскрыты только у выбранного
   коллектора: 67 строк разом не поместились бы рядом с осью. Выбор — кнопки
   с aria-pressed, повторное нажатие на узел снимает отбор. */

export interface TreeNode {
  object_id: number
  name: string
  kind: string
  channels: number
  section_ids: number[]
}

export interface TreeCollector {
  object_id: number
  name: string
  nodes: TreeNode[]
}

const BUTTON = 'text-left w-full px-2 py-1 rounded'

export function ObjectTree({
  tree,
  collector,
  node,
  onCollector,
  onNode,
}: {
  tree: TreeCollector[]
  collector: number | null
  node: number | null
  onCollector: (id: number) => void
  onNode: (id: number | null) => void
}) {
  return (
    <nav aria-label="Дерево объектов" class="text-sm w-64 shrink-0">
      <ul class="flex flex-col gap-1" style="list-style:none; padding:0; margin:0">
        {tree.map((c) => {
          const open = c.object_id === collector
          return (
            <li key={c.object_id}>
              <button
                type="button"
                aria-pressed={open && node == null}
                onClick={() => onCollector(c.object_id)}
                class={BUTTON}
                style={`font-weight:600; color:var(--text-primary); background:${open ? 'var(--bg-surface)' : 'transparent'}`}
              >
                {c.name}
              </button>
              {open && (
                <ul class="flex flex-col pl-3" style="list-style:none; margin:0">
                  {c.nodes.map((n) => {
                    const on = n.object_id === node
                    return (
                      <li key={n.object_id}>
                        <button
                          type="button"
                          aria-pressed={on}
                          onClick={() => onNode(on ? null : n.object_id)}
                          class={BUTTON}
                          style={`color:var(--text-secondary); background:${on ? 'var(--bg-surface)' : 'transparent'}; ${on ? 'outline:1px solid var(--border-strong)' : ''}`}
                        >
                          {n.name} · {n.channels} кан.
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
