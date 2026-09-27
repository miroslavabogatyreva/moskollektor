// US-20. Насколько верить прогнозу (руководитель) — docs/user-stories.md, приёмка Ф-95.
// Пять чисел исходов за период стоят над журналом прогнозов и считаются тем же отбором,
// что журнал: GET /api/forecast-outcomes?from=&to= под ролью пользователя. Сумма пяти
// равна «Найдено: N» журнала — прогноз не теряется и не считается дважды.
import { expect, test, type Page } from '@playwright/test'

const ИСХОДЫ = ['подтвердилось', 'ложная', 'не проверяли', 'горизонт истёк', 'ещё открыт']

// Прошлый полный месяц: «месяц закончился».
function прошлыйМесяц(): { с: string; по: string } {
  const d = new Date()
  const первое = new Date(d.getFullYear(), d.getMonth() - 1, 1)
  const последнее = new Date(d.getFullYear(), d.getMonth(), 0)
  return { с: первое.toLocaleDateString('sv-SE'), по: последнее.toLocaleDateString('sv-SE') }
}

async function сводка(page: Page, с: string, по: string): Promise<number[]> {
  await page.goto(`/log?from=${с}&to=${по}`)
  const блок = page.getByTestId('outcome-summary')
  await expect(блок).toBeVisible({ timeout: 30_000 })
  const числа = []
  for (const исход of ИСХОДЫ) {
    const т = await блок.getByTestId(`outcome-${ИСХОДЫ.indexOf(исход)}`).innerText()
    expect(т, `подпись «${исход}»`).toContain(исход)
    числа.push(Number(т.replace(/\D/g, '')))
  }
  return числа
}

test.describe('руководитель района А', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': 'disp2' } })

  test('US-20 сц. 1: сводка исходов за период', async ({ page }) => {
    const { с, по } = прошлыйМесяц()
    const числа = await сводка(page, с, по)
    const { total } = (await (
      await page.request.get(`/api/forecasts?from=${с}&to=${по}&limit=1`)
    ).json()) as { total: number }
    expect(total, 'у района есть прогнозы за месяц').toBeGreaterThan(0)
    expect(
      числа.reduce((a, b) => a + b, 0),
      'сумма пяти = прогнозов района за месяц',
    ).toBe(total)
    await expect(page.getByTestId('log-total')).toHaveText(`Найдено: ${total}`)
    test.info().annotations.push({
      type: 'замер',
      description: `${с}…${по}, disp2: ${ИСХОДЫ.map((и, i) => `${и} ${числа[i]}`).join(', ')}; всего ${total}`,
    })
  })
})

test.describe('диспетчер ОДС', () => {
  test.use({ extraHTTPHeaders: { 'X-User-Login': 'ods1' } })

  test('US-20 сц. 2: диспетчер ОДС видит весь парк', async ({ page, playwright, baseURL }) => {
    const { с, по } = прошлыйМесяц()
    const парк = await сводка(page, с, по)
    for (const логин of ['disp2', 'tech1']) {
      const ctx = await playwright.request.newContext({
        baseURL,
        ignoreHTTPSErrors: true,
        extraHTTPHeaders: { 'X-User-Login': логин },
      })
      const р = (await (
        await ctx.get(`/api/forecast-outcomes?from=${с}&to=${по}`)
      ).json()) as Record<string, number>
      const район = [р.confirmed, р.false_alarm, р.not_checked, р.horizon_expired, р.open]
      for (const [i, n] of район.entries())
        expect(парк[i], `${ИСХОДЫ[i]}: парк не меньше, чем у ${логин}`).toBeGreaterThanOrEqual(n)
      expect(парк.reduce((a, b) => a + b, 0)).toBeGreaterThan(р.total)
      await ctx.dispose()
    }
  })
})
