import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AITransactionDialog from './AITransactionDialog'
import CopilotPanel from './CopilotPanel'
import * as api from '../services/api'
import { recordUsageFromHeaders, resetAIUsage } from '../services/aiUsage'
import { resetAuthBridge, setUnauthorizedHandler } from '../services/authToken'
import { makeChatResponse, makeParseDraft } from '../test/fixtures'

/**
 * Qué pasa en la interfaz cuando se agotan las 10 consultas inteligentes del día.
 *
 * Un 429 no es una sesión caída y no es el backend apagado: la persona sigue logueada, la
 * conversación sigue en pantalla y el texto que escribió no se pierde. Lo único que cambia
 * es que la IA no está disponible hasta mañana; todo lo manual sigue funcionando.
 */

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    chatCopilot: vi.fn(),
    approveCopilotAction: vi.fn(),
    rejectCopilotAction: vi.fn(),
    parseAITransaction: vi.fn(),
    confirmAITransaction: vi.fn(),
    rejectAITransaction: vi.fn(),
  }
})

const { ApiError } = api

// El texto exacto que arma el backend con AI_DAILY_LIMIT=10. No se muestra "Algo salió
// mal" para este caso: se dice qué pasó y qué se puede seguir haciendo.
const LIMIT_MESSAGE =
  'Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando las ' +
  'funciones manuales de Plata y volver a consultar mañana.'

/** El 429 que arma `api.js` a partir del detalle estructurado del backend. */
function limitError(message = LIMIT_MESSAGE) {
  return new ApiError(message, {
    status: 429,
    detail: {
      code: 'daily_ai_limit_reached',
      message,
      limit: 10,
      used: 10,
      remaining: 0,
      resets_at: '2026-07-30T00:00:00-03:00',
      reset_at: '2026-07-30T00:00:00-03:00',
      timezone: 'America/Argentina/Buenos_Aires',
    },
  })
}

/** Simula las cabeceras que acompañan a cualquier respuesta con cuota, incluido el 429. */
function recordRemaining(remaining, { limit = 10, warnAt = 3 } = {}) {
  recordUsageFromHeaders({
    get: (name) =>
      ({
        'x-ai-daily-limit': String(limit),
        'x-ai-daily-remaining': String(remaining),
        'x-ai-daily-warn-at': String(warnAt),
        'x-ai-daily-reset-at': '2026-07-30T00:00:00-03:00',
      })[name.toLowerCase()] ?? null,
  })
}

beforeEach(() => {
  resetAIUsage()
  resetAuthBridge()
  vi.clearAllMocks()
})

afterEach(() => {
  resetAIUsage()
  resetAuthBridge()
  vi.clearAllMocks()
})

describe('copiloto: aviso de cuota', () => {
  it('avisa cuántas consultas quedan después de responder', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockImplementation(async () => {
      recordRemaining(3)
      return makeChatResponse()
    })
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Te quedan 3 consultas inteligentes por hoy.',
    )
  })

  it('el aviso no es una burbuja de conversación ni queda en el historial', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockImplementation(async () => {
      recordRemaining(2)
      return makeChatResponse({ answer: 'Podés gastar 12.000 por día.' })
    })
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))
    await screen.findByText('Podés gastar 12.000 por día.')

    // El aviso vive fuera de la lista de mensajes: el historial solo tiene la pregunta y
    // la respuesta.
    const aviso = screen.getByRole('status')
    const historial = screen.getByText('Podés gastar 12.000 por día.').closest('ul, ol, li')
    expect(historial === null || !historial.contains(aviso)).toBe(true)
  })
})

describe('copiloto: 429', () => {
  it('muestra el mensaje real del límite', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockRejectedValue(
      limitError(),
    )
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(LIMIT_MESSAGE)
  })

  it('no dice que el backend está desconectado', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockRejectedValue(
      limitError(),
    )
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))
    await screen.findByRole('alert')

    expect(screen.queryByText(/no pudimos conectar|backend/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no está disponible/i)).not.toBeInTheDocument()
  })

  it('no cierra la sesión', async () => {
    const user = userEvent.setup()
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    api.chatCopilot.mockRejectedValue(
      limitError(),
    )
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))
    await screen.findByRole('alert')

    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('conserva la conversación que ya estaba en pantalla', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockResolvedValueOnce(makeChatResponse({ answer: 'Te quedan 12.000.' }))
    render(<CopilotPanel />)

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))
    await screen.findByText('Te quedan 12.000.')

    api.chatCopilot.mockRejectedValueOnce(
      limitError(),
    )
    await user.type(screen.getByRole('textbox'), '¿y mañana?')
    await user.keyboard('{Enter}')
    await screen.findByRole('alert')

    // Ni la respuesta anterior ni la pregunta nueva se borran.
    expect(screen.getByText('Te quedan 12.000.')).toBeInTheDocument()
    expect(screen.getByText('¿Cuánto puedo gastar hoy?')).toBeInTheDocument()
  })
})

describe('escribilo con IA: 429', () => {
  function renderDialog() {
    return render(
      <AITransactionDialog onRegistered={vi.fn()} onFallback={vi.fn()} onClose={vi.fn()} />,
    )
  }

  it('conserva el texto escrito', async () => {
    const user = userEvent.setup()
    api.parseAITransaction.mockRejectedValue(
      limitError(),
    )
    renderDialog()

    const texto = 'Gasté 25 mil en combustible'
    await user.type(screen.getByRole('textbox'), texto)
    await user.click(screen.getByRole('button', { name: /interpretar/i }))
    await screen.findByRole('alert')

    expect(screen.getByRole('textbox')).toHaveValue(texto)
  })

  it('muestra el mensaje real y no crea un borrador', async () => {
    const user = userEvent.setup()
    api.parseAITransaction.mockRejectedValue(
      limitError(),
    )
    renderDialog()

    await user.type(screen.getByRole('textbox'), 'Gasté 25 mil en combustible')
    await user.click(screen.getByRole('button', { name: /interpretar/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(LIMIT_MESSAGE)
    // Sigue en el paso de escritura: no hay borrador que confirmar.
    expect(screen.queryByRole('button', { name: /confirmar/i })).not.toBeInTheDocument()
    expect(api.confirmAITransaction).not.toHaveBeenCalled()
  })

  it('el modal sigue abierto', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    api.parseAITransaction.mockRejectedValue(
      limitError(),
    )
    render(
      <AITransactionDialog onRegistered={vi.fn()} onFallback={vi.fn()} onClose={onClose} />,
    )

    await user.type(screen.getByRole('textbox'), 'Gasté 25 mil en combustible')
    await user.click(screen.getByRole('button', { name: /interpretar/i }))
    await screen.findByRole('alert')

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('avisa cuántas consultas inteligentes quedan tras un uso exitoso', async () => {
    const user = userEvent.setup()
    api.parseAITransaction.mockImplementation(async () => {
      recordRemaining(1)
      return makeParseDraft()
    })
    renderDialog()

    await user.type(screen.getByRole('textbox'), 'Gasté 25 mil en combustible')
    await user.click(screen.getByRole('button', { name: /interpretar/i }))

    // Por texto y no por rol: el paso del borrador tiene su propio `role="status"`.
    expect(await screen.findByText(/Te queda 1 consulta inteligente por hoy\./)).toBeInTheDocument()
  })
})

describe('los contadores no se guardan en el navegador', () => {
  it('no escribe nada en localStorage', () => {
    const setItem = vi.spyOn(window.localStorage, 'setItem')

    recordRemaining(2)

    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })
})
