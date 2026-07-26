import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CopilotPanel from './CopilotPanel'
import * as api from '../services/api'
import { makeChatResponse } from '../test/fixtures'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    chatCopilot: vi.fn(),
    approveCopilotAction: vi.fn(),
    rejectCopilotAction: vi.fn(),
  }
})

const { ApiError } = api

beforeEach(() => vi.clearAllMocks())
afterEach(() => vi.clearAllMocks())

describe('CopilotPanel', () => {
  it('responde a una consulta y muestra qué herramientas usó', async () => {
    api.chatCopilot.mockResolvedValue(makeChatResponse())
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: /¿Cuánto puedo gastar hoy\?/i }))

    expect(await screen.findByText(/Hoy podés gastar/i)).toBeInTheDocument()
    await user.click(screen.getByText(/Cómo lo resolví/i))
    expect(screen.getByText('get_financial_summary')).toBeInTheDocument()
  })

  it('pausa una escritura y la aplica solo al aprobar', async () => {
    api.chatCopilot.mockResolvedValue(
      makeChatResponse({
        intent: 'create_transaction',
        answer: 'Preparé esto para registrar…',
        requires_approval: true,
        pending_action: {
          action_id: '55555555-5555-4555-8555-555555555555',
          kind: 'create_transaction',
          summary: 'gasto de $25.000 en transporte',
          draft: {},
        },
      }),
    )
    api.approveCopilotAction.mockResolvedValue(makeChatResponse({ answer: 'Registré el gasto.' }))
    const onActionApplied = vi.fn()
    const user = userEvent.setup()
    render(<CopilotPanel onActionApplied={onActionApplied} />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'Gasté 25 lucas en nafta')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))

    expect(await screen.findByText(/Requiere tu aprobación/i)).toBeInTheDocument()
    expect(screen.getByText(/Primero aproba o rechaza esta accion/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Escribile al copiloto/i)).toBeDisabled()
    expect(screen.getByRole('button', { name: /Enviar/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Rechazar/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Aprobar y registrar/i })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: /Aprobar y registrar/i }))

    expect(api.approveCopilotAction).toHaveBeenCalledOnce()
    expect(onActionApplied).toHaveBeenCalledOnce()
    expect(await screen.findByText(/Registré el gasto/i)).toBeInTheDocument()
  })

  it('muestra un error seguro si el copiloto falla', async () => {
    api.chatCopilot.mockRejectedValue(new ApiError('El copiloto no está disponible', { status: 503 }))
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'hola')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/no está disponible/i)
  })
})
