// Число со словом в нужной форме: «1 датчик», «2 датчика», «5 датчиков» (MOS-262).
// Форму выбирает Intl.PluralRules('ru-RU'): 11 — «датчиков», 21 — «датчик»,
// 104 — «датчика». Проверяется node-скриптом plural.selfcheck.ts.

export type Формы = [one: string, few: string, many: string]

const правила = new Intl.PluralRules('ru-RU')

export function слово(n: number, [one, few, many]: Формы): string {
  const форма = правила.select(n)
  return форма === 'one' ? one : форма === 'few' ? few : many
}

export const склонить = (n: number, формы: Формы): string => `${n} ${слово(n, формы)}`

export const датчиков = (n: number) => склонить(n, ['датчик', 'датчика', 'датчиков'])
export const участков = (n: number) => склонить(n, ['участок', 'участка', 'участков'])
export const линий = (n: number) => склонить(n, ['линия', 'линии', 'линий'])
export const коллекторов = (n: number) => склонить(n, ['коллектор', 'коллектора', 'коллекторов'])
export const коллекторах = (n: number) => склонить(n, ['коллекторе', 'коллекторах', 'коллекторах'])
// После «из»: «из 1 участка», «из 21 участка», но «из 2 участков».
export const изУчастков = (n: number) => склонить(n, ['участка', 'участков', 'участков'])
