import logoUrl from '../assets/logo.png'

/* Эмблема Москоллектора — из dashboard/logo-blue.png (1100×556), обрезана по
   краям рисунка и сжата до 640×304, 20 КБ. Файл хранит только прозрачность,
   а цвет даёт currentColor: одна картинка рисуется белой в синей шапке
   и фирменным синим #003882 на экране входа. dashboard/logo-white.png в поставку
   не идёт: у него серый фон со свечением, прозрачного белого варианта нет. */
export function Logo({ height }: { height: number }) {
  return (
    <span
      role="img"
      aria-label="Москоллектор"
      class="block shrink-0"
      style={{
        height: `${height}px`,
        aspectRatio: '640 / 304',
        background: 'currentColor',
        mask: `url(${logoUrl}) center / contain no-repeat`,
        WebkitMask: `url(${logoUrl}) center / contain no-repeat`,
      }}
    />
  )
}
