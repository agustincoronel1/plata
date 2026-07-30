import { vi } from 'vitest'

/**
 * Doble del cliente de Supabase para los tests de autenticación.
 *
 * Los tests NUNCA hablan con el proyecto real: no habría red, gastaría cuota y dejaría de
 * ser determinístico. Este doble reproduce solo lo que Plata usa del SDK —getSession,
 * onAuthStateChange, signUp, signInWithPassword y signOut— con el mismo contrato de
 * `{ data, error }` y la misma forma de suscripción.
 */

export function makeSession({
  accessToken = 'token-de-prueba',
  userId = '3f2c1b9e-8a4d-4c1f-9b2e-5d6a7c8e9f01',
  email = 'persona@ejemplo.test',
} = {}) {
  return {
    access_token: accessToken,
    refresh_token: 'refresh-de-prueba',
    expires_in: 3600,
    token_type: 'bearer',
    user: { id: userId, email },
  }
}

/**
 * Crea el doble.
 *
 * - `initialSession`: lo que devuelve `getSession()` al arrancar. `null` simula a alguien
 *   sin sesión; un objeto, una sesión restaurada de una visita anterior.
 * - `deferInitialSession`: deja la primera restauración pendiente hasta llamar a
 *   `resolveInitialSession()`. Sirve para observar el estado de carga.
 */
export function makeSupabaseMock({ initialSession = null, deferInitialSession = false } = {}) {
  const listeners = new Set()
  const unsubscribe = vi.fn(() => listeners.clear())

  let currentSession = initialSession
  let releaseInitial = () => {}
  const initialGate = deferInitialSession
    ? new Promise((resolve) => {
        releaseInitial = resolve
      })
    : Promise.resolve()

  let firstGetSession = true

  const auth = {
    getSession: vi.fn(async () => {
      if (firstGetSession) {
        firstGetSession = false
        await initialGate
      }
      return { data: { session: currentSession }, error: null }
    }),

    onAuthStateChange: vi.fn((callback) => {
      listeners.add(callback)
      return {
        data: {
          subscription: {
            unsubscribe: () => {
              listeners.delete(callback)
              unsubscribe()
            },
          },
        },
      }
    }),

    signUp: vi.fn(async ({ email }) => {
      const session = makeSession({ email })
      currentSession = session
      return { data: { session, user: session.user }, error: null }
    }),

    signInWithPassword: vi.fn(async ({ email }) => {
      const session = makeSession({ email })
      currentSession = session
      return { data: { session, user: session.user }, error: null }
    }),

    signOut: vi.fn(async () => {
      currentSession = null
      return { error: null }
    }),
  }

  return {
    auth,
    unsubscribe,
    /** Libera la restauración inicial cuando se creó con `deferInitialSession`. */
    resolveInitialSession: () => releaseInitial(),
    /** Listeners vivos: sirve para comprobar que no quedan suscripciones duplicadas. */
    listenerCount: () => listeners.size,
    /** Simula un evento del SDK (login en otra pestaña, refresco del token, logout). */
    emit(event, session) {
      currentSession = session ?? null
      for (const listener of [...listeners]) {
        listener(event, session ?? null)
      }
    },
    /** Cambia lo que devolverá `getSession()` de ahí en adelante. */
    setSession(session) {
      currentSession = session
    },
  }
}
