import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { makeSession, makeSupabaseMock } from './auth/test-utils'
import { resetAuthBridge } from './services/authToken'
import { PROFILE, SUMMARY } from './test/fixtures'

/**
 * Circuito completo del lado del navegador: qué se ve sin sesión, qué se ve mientras se
 * restaura, qué se ve con sesión y qué pasa al cerrarla.
 *
 * Supabase está mockeado (los tests no hablan con el proyecto real) y la API financiera
 * también: acá se prueba la protección de la pantalla, no el motor financiero.
 */

const { supabaseStub } = vi.hoisted(() => ({ supabaseStub: { auth: null } }))
vi.mock('./lib/supabase', () => ({ supabase: supabaseStub }))

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchApiHealth: vi.fn(async () => ({ ok: true, version: '0.1.0' })),
    getProfile: vi.fn(),
    getTransactions: vi.fn(async () => []),
    getCommitments: vi.fn(async () => []),
    getDashboardSummary: vi.fn(async () => SUMMARY),
    getSimulations: vi.fn(async () => []),
  }
})

const api = await import('./services/api')

let harness

function mount({ initialSession = null, deferInitialSession = false } = {}) {
  harness = makeSupabaseMock({ initialSession, deferInitialSession })
  supabaseStub.auth = harness.auth
  return render(<App />)
}

beforeEach(() => {
  resetAuthBridge()
  api.getProfile.mockResolvedValue(PROFILE)
})

afterEach(() => {
  resetAuthBridge()
  vi.clearAllMocks()
})

describe('protección de la aplicación', () => {
  it('mientras se recupera la sesión no parpadea ni el login ni el dashboard', async () => {
    mount({ deferInitialSession: true })

    expect(screen.getByText('Recuperando tu sesión…')).toBeInTheDocument()
    expect(screen.queryByLabelText('Correo electrónico')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /tu situación/i })).not.toBeInTheDocument()

    harness.resolveInitialSession()

    expect(await screen.findByLabelText('Correo electrónico')).toBeInTheDocument()
  })

  it('sin sesión muestra la pantalla de acceso, no el dashboard', async () => {
    mount()

    expect(await screen.findByRole('heading', { name: 'Entrá a tu plata.' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Secciones de Vector' })).not.toBeInTheDocument()
  })

  it('sin sesión no se piden datos financieros al backend', async () => {
    mount()
    await screen.findByLabelText('Correo electrónico')

    expect(api.getProfile).not.toHaveBeenCalled()
    expect(api.getTransactions).not.toHaveBeenCalled()
    expect(api.getDashboardSummary).not.toHaveBeenCalled()
  })

  it('con una sesión guardada entra directo a la aplicación', async () => {
    mount({ initialSession: makeSession() })

    expect(await screen.findByRole('heading', { name: 'Tu situación' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Correo electrónico')).not.toBeInTheDocument()
    expect(api.getProfile).toHaveBeenCalled()
  })
})

describe('registro y login desde la pantalla de acceso', () => {
  it('después de crear la cuenta se entra a la aplicación', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByLabelText('Correo electrónico')

    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }))
    await user.type(screen.getByLabelText('Correo electrónico'), 'nueva@ejemplo.test')
    await user.type(screen.getByLabelText('Contraseña'), 'contrasena-larga')
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-larga')
    await user.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))

    expect(await screen.findByRole('heading', { name: 'Tu situación' })).toBeInTheDocument()
  })

  it('una cuenta nueva sin perfil ve el onboarding financiero que ya existía', async () => {
    const user = userEvent.setup()
    api.getProfile.mockRejectedValue(new api.ApiError('No encontrado', { status: 404 }))
    mount()
    await screen.findByLabelText('Correo electrónico')

    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }))
    await user.type(screen.getByLabelText('Correo electrónico'), 'nueva@ejemplo.test')
    await user.type(screen.getByLabelText('Contraseña'), 'contrasena-larga')
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-larga')
    await user.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))

    // El mismo WelcomeScreen de siempre: no se creó un segundo onboarding.
    expect(await screen.findByRole('button', { name: 'Configurar mi cuenta' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tu plata, con contexto.' })).toBeInTheDocument()
  })

  it('después de iniciar sesión se entra a la aplicación', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByLabelText('Correo electrónico')

    await user.type(screen.getByLabelText('Correo electrónico'), 'persona@ejemplo.test')
    await user.type(screen.getByLabelText('Contraseña'), 'contrasena-larga')
    await user.click(screen.getByRole('button', { name: 'Entrar a mi cuenta' }))

    expect(await screen.findByRole('heading', { name: 'Tu situación' })).toBeInTheDocument()
    expect(harness.auth.signInWithPassword).toHaveBeenCalledWith({
      email: 'persona@ejemplo.test',
      password: 'contrasena-larga',
    })
  })
})

describe('cierre de sesión', () => {
  it('el botón de cerrar sesión vuelve a la pantalla de acceso', async () => {
    const user = userEvent.setup()
    mount({ initialSession: makeSession() })
    await screen.findByRole('heading', { name: 'Tu situación' })

    await user.click(screen.getByRole('button', { name: 'Cerrar sesión' }))

    expect(harness.auth.signOut).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('heading', { name: 'Entrá a tu plata.' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Tu situación' })).not.toBeInTheDocument()
  })

  it('un logout en otra pestaña también saca de la aplicación', async () => {
    mount({ initialSession: makeSession() })
    await screen.findByRole('heading', { name: 'Tu situación' })

    harness.emit('SIGNED_OUT', null)

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Entrá a tu plata.' })).toBeInTheDocument(),
    )
  })
})
