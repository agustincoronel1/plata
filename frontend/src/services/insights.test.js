import { describe, expect, it } from 'vitest'

import { buildMonthInsight } from './insights'
import { SUMMARY } from '../test/fixtures'

describe('buildMonthInsight', () => {
  it('sin resumen no dice nada', () => {
    expect(buildMonthInsight(null)).toBeNull()
  })

  it('sin gastos lo dice sin inventar una conclusión', () => {
    const texto = buildMonthInsight({
      ...SUMMARY,
      month_expenses_total: '0.00',
      category_summary: [],
    })
    expect(texto).toBe('Todavía no registraste gastos este mes.')
  })

  it('compara con el mes anterior y nombra la categoría principal', () => {
    // 70.000 este mes contra 100.000 el anterior: 30% menos.
    expect(buildMonthInsight(SUMMARY)).toBe(
      'Vas bien: gastaste 30% menos que el mes pasado y Transporte fue tu categoría principal (50.0%).',
    )
  })

  it('avisa cuando se gastó más', () => {
    const texto = buildMonthInsight({ ...SUMMARY, previous_month_expenses_total: '50000.00' })
    expect(texto).toMatch(/Ojo: gastaste 40% más que el mes pasado/)
  })

  it('sin mes anterior no compara', () => {
    const texto = buildMonthInsight({ ...SUMMARY, previous_month_expenses_total: '0.00' })
    expect(texto).toMatch(/primer mes con gastos registrados/)
  })
})
