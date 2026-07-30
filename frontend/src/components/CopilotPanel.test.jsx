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
  it('sin conversación muestra ejemplos y no consulta nada por su cuenta', () => {
    render(<CopilotPanel />)

    expect(screen.getByText(/Todavía no le preguntaste nada/i)).toBeInTheDocument()
    for (const ejemplo of [
      '¿Cuánto puedo gastar hoy?',
      '¿Qué pagos tengo antes de cobrar?',
      'Gasté 25 mil en combustible.',
      '¿Me conviene comprar una notebook en cuotas?',
    ]) {
      expect(screen.getByRole('button', { name: ejemplo })).toBeEnabled()
    }
    // El panel no dispara ninguna consulta al montarse.
    expect(api.chatCopilot).not.toHaveBeenCalled()
    expect(screen.queryByText(/Pensando…/i)).not.toBeInTheDocument()
  })

  it('los ejemplos desaparecen una vez que hay conversación', async () => {
    api.chatCopilot.mockResolvedValue(makeChatResponse())
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: /^¿Cuánto puedo gastar hoy\?$/i }))

    expect(await screen.findByText(/Podés gastar hasta/)).toBeInTheDocument()
    expect(screen.queryByText(/Todavía no le preguntaste nada/i)).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Gasté 25 mil en combustible.' }),
    ).not.toBeInTheDocument()
  })

  it('responde con la decisión primero y explica cómo lo resolvió en castellano', async () => {
    api.chatCopilot.mockResolvedValue(makeChatResponse())
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: /^¿Cuánto puedo gastar hoy\?$/i }))

    expect(await screen.findByText(/Podés gastar hasta \$210\.000 hoy\./)).toBeInTheDocument()
    await user.click(screen.getByText(/Cómo lo resolví/i))
    expect(screen.getByText(/Tomé tu saldo de \$620\.000/)).toBeInTheDocument()
  })

  it('no expone nombres de herramientas ni campos internos', async () => {
    api.chatCopilot.mockResolvedValue(makeChatResponse())
    const user = userEvent.setup()
    const { container } = render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: /^¿Cuánto puedo gastar hoy\?$/i }))
    expect(await screen.findByText(/Podés gastar hasta/)).toBeInTheDocument()
    await user.click(screen.getByText(/Cómo lo resolví/i))

    const shown = container.textContent
    for (const internal of [
      'get_financial_summary',
      'spendable_total',
      'current_balance',
      'daily_safe_to_spend',
      'is_viable',
      'minimum_margin',
    ]) {
      expect(shown).not.toContain(internal)
    }
  })

  it('sin explicación del backend no muestra la sección "Cómo lo resolví"', async () => {
    api.chatCopilot.mockResolvedValue(makeChatResponse({ structured_answer: null }))
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: /^¿Cuánto puedo gastar hoy\?$/i }))

    expect(await screen.findByText(/Podés gastar hasta/)).toBeInTheDocument()
    expect(screen.queryByText(/Cómo lo resolví/i)).not.toBeInTheDocument()
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

  it('rechazar una acción pendiente no registra nada y libera el chat', async () => {
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
    api.rejectCopilotAction.mockResolvedValue(
      makeChatResponse({ answer: 'Listo, no registré nada.' }),
    )
    const onActionApplied = vi.fn()
    const user = userEvent.setup()
    render(<CopilotPanel onActionApplied={onActionApplied} />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'Gasté 25 lucas en nafta')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))
    await user.click(await screen.findByRole('button', { name: /Rechazar/i }))

    expect(api.rejectCopilotAction).toHaveBeenCalledOnce()
    expect(api.approveCopilotAction).not.toHaveBeenCalled()
    // No se aplicó ninguna escritura y el chat vuelve a estar disponible.
    expect(onActionApplied).not.toHaveBeenCalled()
    expect(await screen.findByText(/no registré nada/i)).toBeInTheDocument()
    expect(screen.queryByText(/Requiere tu aprobación/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Escribile al copiloto/i)).toBeEnabled()
  })

  it('no permite un segundo envío mientras el copiloto está pensando', async () => {
    let resolveChat
    api.chatCopilot.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveChat = resolve
        }),
    )
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'Explicame mi disponible')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))

    // Estado de carga visible; input y botón bloqueados hasta que llegue la respuesta.
    expect(await screen.findByText(/Pensando…/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Escribile al copiloto/i)).toBeDisabled()
    const sendButton = screen.getByRole('button', { name: /Enviar/i })
    expect(sendButton).toBeDisabled()
    await user.click(sendButton)
    expect(api.chatCopilot).toHaveBeenCalledTimes(1)

    resolveChat(makeChatResponse())
    expect(await screen.findByText(/Podés gastar hasta/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Escribile al copiloto/i)).toBeEnabled()
  })

  it('muestra el mensaje de timeout de la IA y no el de backend apagado', async () => {
    api.chatCopilot.mockRejectedValue(
      new ApiError('La IA tardó más de lo esperado. Intentá nuevamente.', {
        status: 0,
        timeout: true,
      }),
    )
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'hola')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/La IA tardó más de lo esperado/i)
    expect(screen.queryByText(/Revisá que el backend esté activo/i)).not.toBeInTheDocument()
    // Sin reintento automático: la llamada se hizo una sola vez y el input vuelve a habilitarse.
    expect(api.chatCopilot).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText(/Escribile al copiloto/i)).toBeEnabled()
  })

  it('no aprueba dos veces la misma acción pendiente', async () => {
    api.chatCopilot.mockResolvedValue(
      makeChatResponse({
        requires_approval: true,
        pending_action: {
          action_id: '55555555-5555-4555-8555-555555555555',
          kind: 'create_transaction',
          summary: 'gasto de $25.000 en transporte',
          draft: {},
        },
      }),
    )
    let resolveApprove
    api.approveCopilotAction.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveApprove = resolve
        }),
    )
    const user = userEvent.setup()
    render(<CopilotPanel />)

    await user.type(screen.getByLabelText(/Escribile al copiloto/i), 'Gasté 25 lucas en nafta')
    await user.click(screen.getByRole('button', { name: /Enviar/i }))

    const approve = await screen.findByRole('button', { name: /Aprobar y registrar/i })
    await user.click(approve)
    expect(approve).toBeDisabled()
    await user.click(approve)
    await user.click(screen.getByRole('button', { name: /Rechazar/i }))

    expect(api.approveCopilotAction).toHaveBeenCalledTimes(1)
    expect(api.rejectCopilotAction).not.toHaveBeenCalled()

    resolveApprove(makeChatResponse({ answer: 'Registré el gasto.' }))
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
