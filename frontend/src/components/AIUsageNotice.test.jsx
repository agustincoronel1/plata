import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import AIUsageNotice from './AIUsageNotice'

/**
 * El aviso tiene que aparecer justo cuando hace falta y callarse el resto del tiempo:
 * alguien que usa Vector dos veces por día no debería enterarse de que hay un límite.
 *
 * Habla de "consultas inteligentes" porque eso es lo que se cuenta: una sola cuota diaria
 * para toda la IA, no una por operación.
 */

function usage({ remaining, limit = 10, warnAt = 3 } = {}) {
  return { remaining, limit, warnAt, warning: remaining > 0 && remaining <= warnAt }
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

describe('qué dice', () => {
  it('avisa en plural al llegar al umbral', () => {
    render(<AIUsageNotice usage={usage({ remaining: 3 })} />)

    expect(aviso()).toHaveTextContent('Te quedan 3 consultas inteligentes por hoy.')
    expect(aviso()).toHaveTextContent('Se renueva mañana.')
  })

  it('conjuga en singular cuando queda una sola', () => {
    render(<AIUsageNotice usage={usage({ remaining: 1 })} />)

    expect(aviso()).toHaveTextContent('Te queda 1 consulta inteligente por hoy.')
  })

  it('avisa que la última ya se usó', () => {
    render(<AIUsageNotice usage={usage({ remaining: 0 })} />)

    expect(aviso()).toHaveTextContent('Usaste tu última consulta inteligente de hoy.')
  })

  it('no habla de una operación en particular: la cuota es una sola', () => {
    render(<AIUsageNotice usage={usage({ remaining: 2 })} />)

    expect(aviso()).not.toHaveTextContent('copiloto')
    expect(aviso()).not.toHaveTextContent('interpretaci')
  })
})
