import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { daysUntilLabel, formatDate, formatMoney } from './format'

describe('formatMoney', () => {
  it('formatea un string Decimal como moneda argentina', () => {
    // El separador de miles argentino es el punto.
    expect(formatMoney('620000.00')).toMatch(/620\.000/)
    expect(formatMoney('120000')).toMatch(/120\.000/)
  })

  it('devuelve un placeholder ante valores nulos o inválidos', () => {
    expect(formatMoney(null)).toBe('$ —')
    expect(formatMoney('')).toBe('$ —')
    expect(formatMoney('no-es-numero')).toBe('$ —')
  })
})

describe('daysUntilLabel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 6, 24)) // 24 de julio de 2026, hora local
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('cuenta los días que faltan hasta la fecha', () => {
    expect(daysUntilLabel('2026-08-01')).toBe('8')
  })

  it('dice "Hoy" cuando la fecha es hoy', () => {
    expect(daysUntilLabel('2026-07-24')).toBe('Hoy')
  })

  it('dice "Fecha vencida" cuando ya pasó', () => {
    expect(daysUntilLabel('2026-07-20')).toBe('Fecha vencida')
  })

  it('devuelve un guion cuando no hay fecha', () => {
    expect(daysUntilLabel(null)).toBe('—')
  })
})

describe('formatDate', () => {
  it('formatea una fecha ISO como texto legible', () => {
    expect(formatDate('2026-08-01')).toMatch(/2026/)
  })

  it('devuelve un guion ante una fecha vacía', () => {
    expect(formatDate(null)).toBe('—')
  })
})
