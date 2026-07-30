import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AI_USAGE_KINDS } from '../services/aiUsage'
import AIUsageNotice from './AIUsageNotice'

/**
 * El aviso tiene que aparecer justo cuando hace falta, decir de qué operación se trata y
 * callarse el resto del tiempo: alguien que usa Plata dos veces por día no debería
 * enterarse de que hay un límite.
 */

function usage({ kind = AI_USAGE_KINDS.copilotChat, remaining, limit = 20, warnAt = 3 } = {}) {
  return { kind, remaining, limit, warnAt, warning: remaining > 0 && remaining <= warnAt }
}

const aviso = () => screen.getByRole('status')

describe('cuándo aparece', () => {
  it('no aparece si todavía sobra cuota', () => {
    render(<AIUsageNotice usage={usage({ remaining: 8 })} />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('no aparece con un uso más que el umbral', () => {
    render(<AIUsageNotice usage={usage({ remaining: 4, warnAt: 3 })} />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('no aparece si no hay dato de cuota', () => {
    render(<AIUsageNotice usage={null} />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('es un aviso educado, no una alerta que interrumpa', () => {
    render(<AIUsageNotice usage={usage({ remaining: 2 })} />)

    expect(aviso()).toHaveAttribute('aria-live', 'polite')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('consultas al copiloto', () => {
  it('avisa en plural al llegar al umbral', () => {
    render(<AIUsageNotice usage={usage({ remaining: 3 })} />)

    expect(aviso()).toHaveTextContent('Te quedan 3 consultas al copiloto por hoy.')
    expect(aviso()).toHaveTextContent('Se renueva mañana.')
  })

  it('conjuga en singular cuando queda una sola', () => {
    render(<AIUsageNotice usage={usage({ remaining: 1 })} />)

    expect(aviso()).toHaveTextContent('Te queda 1 consulta al copiloto por hoy.')
  })

  it('avisa que la última ya se usó', () => {
    render(<AIUsageNotice usage={usage({ remaining: 0 })} />)

    expect(aviso()).toHaveTextContent('Usaste tu última consulta al copiloto de hoy.')
  })
})

describe('interpretaciones con IA', () => {
  const parse = (remaining) => usage({ kind: AI_USAGE_KINDS.transactionParse, remaining, limit: 10 })

  it('avisa en plural al llegar al umbral', () => {
    render(<AIUsageNotice usage={parse(3)} />)

    expect(aviso()).toHaveTextContent('Te quedan 3 interpretaciones con IA por hoy.')
  })

  it('conjuga en singular cuando queda una sola', () => {
    render(<AIUsageNotice usage={parse(1)} />)

    expect(aviso()).toHaveTextContent('Te queda 1 interpretación con IA por hoy.')
  })

  it('avisa que la última ya se usó', () => {
    render(<AIUsageNotice usage={parse(0)} />)

    expect(aviso()).toHaveTextContent('Usaste tu última interpretación con IA de hoy.')
  })

  it('no usa el texto del copiloto', () => {
    render(<AIUsageNotice usage={parse(2)} />)

    expect(aviso()).not.toHaveTextContent('copiloto')
  })
})
