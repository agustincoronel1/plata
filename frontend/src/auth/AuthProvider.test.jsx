import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAccessToken, notifyUnauthorized, resetAuthBridge } from '../services/authToken'
import AuthProvider from './AuthProvider'
import { useAuth } from './AuthContext'
import { makeSession, makeSupabaseMock } from './test-utils'

// El SDK real nunca se carga en los tests: no hay red ni proyecto de Supabase detrás.
const { supabaseStub } = vi.hoisted(() => ({ supabaseStub: { auth: null } }))
vi.mock('../lib/supabase', () => ({ supabase: supabaseStub }))

let harness

function mount({ initialSession = null, deferInitialSession = false } = {}) {
  harness = makeSupabaseMock({ initialSession, deferInitialSession })
  supabaseStub.auth = harness.auth
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

/**
 * Componente de prueba: expone el estado de la sesión, las tres operaciones y el mensaje
 * de error que devuelve cada una (que es como lo consume la pantalla de acceso real).
 */
function Probe() {
  const { session, user, loading, signUp, signIn, signOut } = useAuth()
  const [error, setError] = useState(null)

  async function run(operation) {
    setError(null)
    try {
      await operation()
    } catch (failure) {
      setError(failure.message)
    }
  }

  if (loading) {
    return <p>Recuperando tu sesión…</p>
  }

  return (
    <div>
      <p data-testid="estado">{session ? `dentro:${user.email}` : 'fuera'}</p>
      {error && <p data-testid="error">{error}</p>}
      <button
        type="button"
        onClick={() => run(() => signUp('nueva@ejemplo.test', 'contrasena-larga'))}
      >
        Registrarme
      </button>
      <button
        type="button"
        onClick={() => run(() => signIn('persona@ejemplo.test', 'contrasena-larga'))}
      >
        Entrar
      </button>
      <button type="button" onClick={() => run(() => signOut())}>
        Salir
      </button>
    </div>
  )
}

beforeEach(() => {
  resetAuthBridge()
})

afterEach(() => {
  resetAuthBridge()
  vi.clearAllMocks()
})

describe('restauración de la sesión', () => {
  it('muestra el estado de carga mientras se recupera la sesión guardada', async () => {
    mount({ deferInitialSession: true })

    expect(screen.getByText('Recuperando tu sesión…')).toBeInTheDocument()
    expect(screen.queryByTestId('estado')).not.toBeInTheDocument()

    harness.resolveInitialSession()

    expect(await screen.findByTestId('estado')).toHaveTextContent('fuera')
  })

  it('sin sesión guardada termina la carga como usuario deslogueado', async () => {
    mount()

    expect(await screen.findByTestId('estado')).toHaveTextContent('fuera')
  })

  it('con una sesión guardada la restaura sin pedir credenciales', async () => {
    mount({ initialSession: makeSession({ email: 'vuelve@ejemplo.test' }) })

    expect(await screen.findByTestId('estado')).toHaveTextContent('dentro:vuelve@ejemplo.test')
    expect(harness.auth.signInWithPassword).not.toHaveBeenCalled()
  })

  it('una sesión ilegible deja la aplicación deslogueada, no colgada', async () => {
    harness = makeSupabaseMock()
    harness.auth.getSession = vi.fn(async () => {
      throw new Error('storage corrupto')
    })
    supabaseStub.auth = harness.auth

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    expect(await screen.findByTestId('estado')).toHaveTextContent('fuera')
  })
})

describe('registro, login y logout', () => {
  it('el registro deja la sesión iniciada', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')

    await user.click(screen.getByRole('button', { name: 'Registrarme' }))

    expect(await screen.findByTestId('estado')).toHaveTextContent('dentro:nueva@ejemplo.test')
    expect(harness.auth.signUp).toHaveBeenCalledWith({
      email: 'nueva@ejemplo.test',
      password: 'contrasena-larga',
    })
  })

  it('el login deja la sesión iniciada', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')

    await user.click(screen.getByRole('button', { name: 'Entrar' }))

    expect(await screen.findByTestId('estado')).toHaveTextContent('dentro:persona@ejemplo.test')
  })

  it('el logout cierra la sesión en Supabase y limpia el estado local', async () => {
    const user = userEvent.setup()
    mount({ initialSession: makeSession() })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))

    await user.click(screen.getByRole('button', { name: 'Salir' }))

    expect(harness.auth.signOut).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('fuera'))
  })

  it('un error de registro llega traducido al español y sin sesión', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')
    supabaseStub.auth.signUp = vi.fn(async () => ({
      data: { session: null, user: null },
      error: { code: 'user_already_exists', message: 'User already registered' },
    }))

    await user.click(screen.getByRole('button', { name: 'Registrarme' }))

    expect(await screen.findByTestId('error')).toHaveTextContent(
      'Ya existe una cuenta con ese correo. Probá iniciar sesión.',
    )
    expect(screen.getByTestId('estado')).toHaveTextContent('fuera')
  })

  it('un registro sin sesión devuelta se avisa en lugar de dejar la pantalla vacía', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')
    supabaseStub.auth.signUp = vi.fn(async () => ({ data: { session: null }, error: null }))

    await user.click(screen.getByRole('button', { name: 'Registrarme' }))

    expect(await screen.findByTestId('error')).toHaveTextContent('no quedó iniciada la sesión')
    expect(screen.getByTestId('estado')).toHaveTextContent('fuera')
  })

  it('un error de login llega traducido y no inicia la sesión', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')
    supabaseStub.auth.signInWithPassword = vi.fn(async () => ({
      data: { session: null, user: null },
      error: { code: 'invalid_credentials', message: 'Invalid login credentials' },
    }))

    await user.click(screen.getByRole('button', { name: 'Entrar' }))

    expect(await screen.findByTestId('error')).toHaveTextContent('Correo o contraseña incorrectos.')
    expect(screen.getByTestId('estado')).toHaveTextContent('fuera')
  })

  it('el mensaje crudo del proveedor nunca se muestra tal cual', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByTestId('estado')
    supabaseStub.auth.signInWithPassword = vi.fn(async () => ({
      data: { session: null, user: null },
      error: { code: 'raro_y_sin_mapear', message: 'AuthApiError: something went wrong (500)' },
    }))

    await user.click(screen.getByRole('button', { name: 'Entrar' }))

    const error = await screen.findByTestId('error')
    expect(error).toHaveTextContent('No pudimos completar la operación. Intentá de nuevo.')
    expect(error).not.toHaveTextContent('AuthApiError')
  })
})

describe('suscripción a onAuthStateChange', () => {
  it('registra un único listener', async () => {
    mount()
    await screen.findByTestId('estado')

    expect(harness.auth.onAuthStateChange).toHaveBeenCalledTimes(1)
    expect(harness.listenerCount()).toBe(1)
  })

  it('se desuscribe al desmontar y no deja listeners vivos', async () => {
    const { unmount } = mount()
    await screen.findByTestId('estado')

    unmount()

    expect(harness.unsubscribe).toHaveBeenCalled()
    expect(harness.listenerCount()).toBe(0)
  })

  it('un evento del SDK actualiza la sesión (login o logout en otra pestaña)', async () => {
    mount()
    await screen.findByTestId('estado')

    harness.emit('SIGNED_IN', makeSession({ email: 'otra-pestania@ejemplo.test' }))
    await waitFor(() =>
      expect(screen.getByTestId('estado')).toHaveTextContent('dentro:otra-pestania@ejemplo.test'),
    )

    harness.emit('SIGNED_OUT', null)
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('fuera'))
  })

  it('un evento posterior al desmontaje no actualiza el estado', async () => {
    const { unmount } = mount()
    await screen.findByTestId('estado')

    unmount()
    harness.emit('SIGNED_IN', makeSession())

    // Si el listener siguiera vivo, React avisaría de una actualización sobre un
    // componente desmontado. No queda ninguno.
    expect(harness.listenerCount()).toBe(0)
  })
})

describe('puente con el cliente HTTP', () => {
  it('publica el token de la sesión vigente', async () => {
    mount({ initialSession: makeSession({ accessToken: 'token-vigente' }) })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))

    await expect(getAccessToken()).resolves.toBe('token-vigente')
  })

  it('sin sesión no hay token que publicar', async () => {
    mount()
    await screen.findByTestId('estado')

    await expect(getAccessToken()).resolves.toBeNull()
  })

  it('lee la sesión en cada consulta: un token renovado se usa enseguida', async () => {
    mount({ initialSession: makeSession({ accessToken: 'token-viejo' }) })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))
    await expect(getAccessToken()).resolves.toBe('token-viejo')

    harness.setSession(makeSession({ accessToken: 'token-renovado' }))

    await expect(getAccessToken()).resolves.toBe('token-renovado')
  })

  it('al desmontar deja de publicar el token', async () => {
    const { unmount } = mount({ initialSession: makeSession() })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))

    unmount()

    await expect(getAccessToken()).resolves.toBeNull()
  })

  it('un 401 del backend cierra la sesión y vuelve al estado deslogueado', async () => {
    mount({ initialSession: makeSession() })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))

    notifyUnauthorized()

    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('fuera'))
    expect(harness.auth.signOut).toHaveBeenCalledTimes(1)
  })

  it('varios 401 seguidos no encadenan cierres de sesión', async () => {
    mount({ initialSession: makeSession() })
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('dentro:'))

    notifyUnauthorized()
    await waitFor(() => expect(screen.getByTestId('estado')).toHaveTextContent('fuera'))
    notifyUnauthorized()
    notifyUnauthorized()

    expect(harness.auth.signOut).toHaveBeenCalledTimes(1)
  })
})
