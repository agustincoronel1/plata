import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { makeSession, makeSupabaseMock } from '../auth/test-utils'
import CopilotPanel from '../components/CopilotPanel'
import { resetAIUsage } from '../services/aiUsage'
import { resetAuthBridge } from '../services/authToken'
import { PROFILE, SUMMARY } from '../test/fixtures'
import BackendStatusProvider from './BackendStatusProvider'

/**
 * El arranque del backend, visto desde la aplicación entera.
 *
 * Dos cosas que no se ven en el test del gate aislado:
 *
 * 1. Mientras `/health` no conteste, el dashboard ni siquiera se monta, así que no salen
 *    cinco peticiones a la vez contra un servidor que está arrancando.
 * 2. Los errores que NO son arranque siguen su camino de siempre: un 401 cierra la sesión
 *    y un 429 muestra el límite diario. Ninguno de los dos dice "estamos iniciando".
 */

const { supabaseStub } = vi.hoisted(() => ({ supabaseStub: { auth: null } }))
vi.mock('../lib/supabase', () => ({ supabase: supabaseStub }))

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchApiHealth: vi.fn(),
    getProfile: vi.fn(),
    getTransactions: vi.fn(async () => []),
    getCommitments: vi.fn(async () => []),
    getDashboardSummary: vi.fn(async () => SUMMARY),
    getSimulations: vi.fn(async () => []),
    chatCopilot: vi.fn(),
  }
})

const api = await import('../services/api')
const { ApiError } = api

const HEALTHY = { ok: true, version: '0.1.0', status: 200, waking: false }

const WAKING_TEXT = /Estamos iniciando el servidor de Vector\./
const LIMIT_MESSAGE =
  'Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando las ' +
  'funciones manuales de Vector y volver a consultar mañana.'

let harness

function mountApp({ initialSession = makeSession() } = {}) {
  harness = makeSupabaseMock({ initialSession })
  supabaseStub.auth = harness.auth
  return render(<App />)
}

beforeEach(() => {
  resetAuthBridge()
  resetAIUsage()
  api.fetchApiHealth.mockResolvedValue(HEALTHY)
  api.getProfile.mockResolvedValue(PROFILE)
})

afterEach(() => {
  resetAuthBridge()
  resetAIUsage()
  vi.clearAllMocks()
})

describe('el dashboard espera a que el backend esté disponible', () => {
  it('no pide nada mientras /health no responda', async () => {
    let responder
    api.fetchApiHealth.mockReturnValue(
      new Promise((resolve) => {
        responder = resolve
      }),
    )
    mountApp()

    // Sesión restaurada y pantalla de espera, pero ni una sola consulta financiera.
    expect(await screen.findByText('Conectando con Vector…')).toBeInTheDocument()
    expect(api.getProfile).not.toHaveBeenCalled()
    expect(api.getTransactions).not.toHaveBeenCalled()
    expect(api.getCommitments).not.toHaveBeenCalled()
    expect(api.getDashboardSummary).not.toHaveBeenCalled()
    expect(api.getSimulations).not.toHaveBeenCalled()

    await act(async () => {
      responder(HEALTHY)
    })

    // Cuando responde, la información se carga sola: nadie tuvo que recargar la página.
    expect(await screen.findByRole('heading', { name: 'Tu situación' })).toBeInTheDocument()
    expect(api.getProfile).toHaveBeenCalledTimes(1)
  })

  it('la pantalla de acceso no espera al backend: el login es contra Supabase', async () => {
    api.fetchApiHealth.mockReturnValue(new Promise(() => {}))
    mountApp({ initialSession: null })

    expect(await screen.findByLabelText('Correo electrónico')).toBeInTheDocument()
    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
  })
})

describe('lo que no es un arranque', () => {
  it('un 401 no se interpreta como servidor dormido', async () => {
    // Cerrar la sesión ante un 401 lo hace `api.js` al leer la respuesta real
    // (`notifyUnauthorized`, con sus tests en services/api.auth.test.js). Acá el cliente
    // HTTP está mockeado, así que lo que se comprueba es lo otro: que el 401 no active el
    // ciclo de arranque ni muestre el mensaje de "estamos iniciando".
    api.getProfile.mockRejectedValue(new ApiError('Tu sesión expiró.', { status: 401 }))
    mountApp()

    await screen.findByRole('heading', { name: 'Algo salió mal' })

    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
    expect(screen.queryByText(/No pudimos iniciar el servidor/)).not.toBeInTheDocument()
    // No reabre el ciclo de arranque: se consultó /health una sola vez.
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
  })

  it('un 500 conserva el error genérico del dashboard', async () => {
    api.getProfile.mockRejectedValue(new ApiError('Ocurrió un error inesperado.', { status: 500 }))
    mountApp()

    expect(await screen.findByRole('heading', { name: 'Algo salió mal' })).toBeInTheDocument()
    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
  })

  it('un 429 muestra el límite diario y no el mensaje de arranque', async () => {
    const user = userEvent.setup()
    api.chatCopilot.mockRejectedValue(
      new ApiError(LIMIT_MESSAGE, {
        status: 429,
        detail: {
          code: 'daily_ai_limit_reached',
          message: LIMIT_MESSAGE,
          limit: 10,
          used: 10,
          remaining: 0,
          reset_at: '2026-08-04T00:00:00-03:00',
          resets_at: '2026-08-04T00:00:00-03:00',
          timezone: 'America/Argentina/Buenos_Aires',
        },
      }),
    )
    render(
      <BackendStatusProvider>
        <CopilotPanel />
      </BackendStatusProvider>,
    )
    await waitFor(() => expect(api.fetchApiHealth).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(LIMIT_MESSAGE)
    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
    expect(screen.queryByText(/Algo salió mal/)).not.toBeInTheDocument()
  })
})
