import { afterEach, describe, expect, it } from 'vitest'

import { AI_USAGE_KINDS, getAIUsage, recordUsageFromHeaders, resetAIUsage } from './aiUsage'

/**
 * El frontend no calcula la cuota: solo repite lo último que dijo el backend. Estos tests
 * fijan esa lectura y, sobre todo, que no rompa cuando las cabeceras no están.
 */

function headers(map) {
  return { get: (name) => map[name.toLowerCase()] ?? null }
}

function usageHeaders({ kind = 'copilot_chat', limit = 20, remaining = 5, warnAt = 3 } = {}) {
  return headers({
    'x-ai-daily-kind': kind,
    'x-ai-daily-limit': String(limit),
    'x-ai-daily-remaining': String(remaining),
    'x-ai-daily-warn-at': String(warnAt),
  })
}

afterEach(() => {
  resetAIUsage()
})

describe('lectura de las cabeceras', () => {
  it('registra la cuota que informó el backend', () => {
    recordUsageFromHeaders(usageHeaders({ limit: 20, remaining: 12 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat)).toMatchObject({
      limit: 20,
      remaining: 12,
      warnAt: 3,
    })
  })

  it('guarda cada operación por separado', () => {
    recordUsageFromHeaders(usageHeaders({ kind: 'copilot_chat', remaining: 12 }))
    recordUsageFromHeaders(usageHeaders({ kind: 'transaction_parse', limit: 10, remaining: 4 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).remaining).toBe(12)
    expect(getAIUsage(AI_USAGE_KINDS.transactionParse).remaining).toBe(4)
  })

  it('la última respuesta pisa a la anterior', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 5 }))
    recordUsageFromHeaders(usageHeaders({ remaining: 4 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).remaining).toBe(4)
  })

  it('sin datos todavía devuelve null en vez de inventar un número', () => {
    expect(getAIUsage(AI_USAGE_KINDS.copilotChat)).toBeNull()
  })
})

describe('tolerancia a respuestas sin cuota', () => {
  it('una respuesta sin cabeceras no registra nada', () => {
    recordUsageFromHeaders(headers({}))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat)).toBeNull()
  })

  it('no rompe si la respuesta no expone cabeceras', () => {
    expect(() => recordUsageFromHeaders(undefined)).not.toThrow()
    expect(() => recordUsageFromHeaders({})).not.toThrow()
    expect(getAIUsage(AI_USAGE_KINDS.copilotChat)).toBeNull()
  })

  it('ignora valores que no son números', () => {
    recordUsageFromHeaders(
      headers({
        'x-ai-daily-kind': 'copilot_chat',
        'x-ai-daily-limit': 'muchos',
        'x-ai-daily-remaining': 'pocos',
      }),
    )

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat)).toBeNull()
  })
})

describe('cuándo avisar', () => {
  it('no avisa mientras sobra cuota', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 4, warnAt: 3 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).warning).toBe(false)
  })

  it('avisa al llegar al umbral', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 3, warnAt: 3 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).warning).toBe(true)
  })

  it('sigue avisando con un uso restante', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 1, warnAt: 3 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).warning).toBe(true)
  })

  it('agotada deja de ser un aviso y pasa a ser un bloqueo', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 0, warnAt: 3 }))

    const usage = getAIUsage(AI_USAGE_KINDS.copilotChat)
    expect(usage.warning).toBe(false)
    expect(usage.exhausted).toBe(true)
  })

  it('usa el umbral que manda el backend, no uno propio', () => {
    recordUsageFromHeaders(usageHeaders({ remaining: 5, warnAt: 6 }))

    expect(getAIUsage(AI_USAGE_KINDS.copilotChat).warning).toBe(true)
  })
})
