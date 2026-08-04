import { afterEach, describe, expect, it } from 'vitest'

import { getAIUsage, recordUsageFromHeaders, resetAIUsage } from './aiUsage'

/**
 * El frontend no calcula la cuota: solo repite lo último que dijo el backend. Estos tests
 * fijan esa lectura y, sobre todo, que no rompa cuando las cabeceras no están.
 *
 * Es un solo contador: el copiloto y la interpretación de movimientos comparten el mismo
 * cupo diario, así que la última respuesta de cualquiera de los dos es la que vale.
 */

function headers(map) {
  return { get: (name) => map[name.toLowerCase()] ?? null }
}

function usageHeaders({ limit = 10, remaining = 5, warnAt = 3, resetAt } = {}) {
  return headers({
    'x-ai-daily-limit': String(limit),
    'x-ai-daily-remaining': String(remaining),
    'x-ai-daily-warn-at': String(warnAt),
    'x-ai-daily-reset-at': resetAt ?? '2026-08-04T00:00:00-03:00',
  })
}

afterEach(() => {
  resetAIUsage()
})

describe('lectura de las cabeceras', () => {
  it('registra la cuota que informó el backend', () => {
    recordUsageFromHeaders(usageHeaders({ limit: 10, remaining: 7 }))

    expect(getAIUsage()).toMatchObject({
      limit: 10,
      remaining: 7,
      warnAt: 3,
      resetAt: '2026-08-04T00:00:00-03:00',
    })
  })

  it('hay un solo contador para toda la IA', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 7 }))
    recordUsageFromHeaders(usageHeaders({ remaining: 6 }))

    expect(getAIUsage().remaining).toBe(6)
  })

  it('la última respuesta pisa a la anterior', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 5 }))
    recordUsageFromHeaders(usageHeaders({ remaining: 4 }))

    expect(getAIUsage().remaining).toBe(4)
  })

  it('sin datos todavía devuelve null en vez de inventar un número', () => {
    expect(getAIUsage()).toBeNull()
  })
})

describe('tolerancia a respuestas sin cuota', () => {
  it('una respuesta sin cabeceras no registra nada', () => {
    recordUsageFromHeaders(headers({}))

    expect(getAIUsage()).toBeNull()
  })

  it('no rompe si la respuesta no expone cabeceras', () => {
    expect(() => recordUsageFromHeaders(undefined)).not.toThrow()
    expect(() => recordUsageFromHeaders({})).not.toThrow()
    expect(getAIUsage()).toBeNull()
  })

  it('ignora valores que no son números', () => {
    recordUsageFromHeaders(
      headers({
        'x-ai-daily-limit': 'muchos',
        'x-ai-daily-remaining': 'pocos',
      }),
    )

    expect(getAIUsage()).toBeNull()
  })

  it('sin la cabecera de reinicio guarda el resto igual', () => {
    recordUsageFromHeaders(
      headers({ 'x-ai-daily-limit': '10', 'x-ai-daily-remaining': '2' }),
    )

    expect(getAIUsage()).toMatchObject({ limit: 10, remaining: 2, resetAt: null })
  })
})

describe('cuándo avisar', () => {
  it('no avisa mientras sobra cuota', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 4, warnAt: 3 }))

    expect(getAIUsage().warning).toBe(false)
  })

  it('avisa al llegar al umbral', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 3, warnAt: 3 }))

    expect(getAIUsage().warning).toBe(true)
  })

  it('sigue avisando con un uso restante', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 1, warnAt: 3 }))

    expect(getAIUsage().warning).toBe(true)
  })

  it('agotada deja de ser un aviso y pasa a ser un bloqueo', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 0, warnAt: 3 }))

    const usage = getAIUsage()
    expect(usage.warning).toBe(false)
    expect(usage.exhausted).toBe(true)
  })

  it('usa el umbral que manda el backend, no uno propio', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 5, warnAt: 6 }))

    expect(getAIUsage().warning).toBe(true)
  })
})
