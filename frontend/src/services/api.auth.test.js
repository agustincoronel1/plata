import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAIUsage, resetAIUsage } from './aiUsage'
import {
  AI_LIMIT_MESSAGE,
  ApiError,
  RATE_LIMIT_MESSAGE,
  UNAUTHORIZED_MESSAGE,
  chatCopilot,
  getCurrentUser,
  getProfile,
} from './api'
import {
  resetAuthBridge,
  setAccessTokenProvider,
  setUnauthorizedHandler,
} from './authToken'

/**
 * El JWT viaja en `api.js` y en ningún otro lado: ningún componente arma el header.
 * Estos tests fijan ese contrato y el comportamiento ante un 401.
 */

function jsonResponse(payload, { status = 200 } = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload }
}

function headersOf(fetchMock, call = 0) {
  return fetchMock.mock.calls[call][1].headers
}

let fetchMock

beforeEach(() => {
  resetAuthBridge()
  resetAIUsage()
  fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  resetAuthBridge()
  resetAIUsage()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('envío del JWT', () => {
  it('agrega Authorization: Bearer con el token de la sesión', async () => {
    setAccessTokenProvider(async () => 'token-de-la-sesion')

    await getProfile()

    expect(headersOf(fetchMock).Authorization).toBe('Bearer token-de-la-sesion')
  })

  it('sin sesión la petición sale sin Authorization', async () => {
    await getProfile()

    expect(headersOf(fetchMock).Authorization).toBeUndefined()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('con la sesión cerrada tampoco se manda un header vacío', async () => {
    setAccessTokenProvider(async () => null)

    await getProfile()

    expect(headersOf(fetchMock).Authorization).toBeUndefined()
  })

  it('pregunta el token en cada petición: usa el renovado, no el primero', async () => {
    const tokens = ['token-1', 'token-2']
    setAccessTokenProvider(async () => tokens.shift())

    await getProfile()
    await getProfile()

    expect(headersOf(fetchMock, 0).Authorization).toBe('Bearer token-1')
    expect(headersOf(fetchMock, 1).Authorization).toBe('Bearer token-2')
  })

  it('un fallo al leer la sesión no rompe la petición', async () => {
    setAccessTokenProvider(async () => {
      throw new Error('storage no disponible')
    })

    await expect(getProfile()).resolves.toEqual({ ok: true })
    expect(headersOf(fetchMock).Authorization).toBeUndefined()
  })

  it('el token no se filtra a la URL ni al cuerpo', async () => {
    setAccessTokenProvider(async () => 'token-secreto')

    await getProfile()

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).not.toContain('token-secreto')
    expect(options.body).toBeUndefined()
  })

  it('conserva el resto de los headers y el payload', async () => {
    setAccessTokenProvider(async () => 'token-de-la-sesion')
    const { createTransaction } = await import('./api')

    await createTransaction({ amount: '1000', type: 'expense' })

    const [, options] = fetchMock.mock.calls[0]
    expect(options.headers).toMatchObject({
      Accept: 'application/json',
      'Content-Type': 'application/json',
      Authorization: 'Bearer token-de-la-sesion',
    })
    expect(JSON.parse(options.body)).toEqual({ amount: '1000', type: 'expense' })
  })
})

describe('el frontend no elige de quién son los datos', () => {
  /**
   * El backend resuelve el usuario con el `sub` del JWT. Si el frontend mandara además un
   * identificador, sería una segunda fuente de verdad —y la que un atacante puede editar—,
   * así que ninguna llamada financiera debe llevarlo.
   */
  it('ninguna petición manda user_id en el cuerpo', async () => {
    setAccessTokenProvider(async () => 'token-de-la-sesion')
    const api = await import('./api')

    await api.createTransaction({ type: 'expense', amount: '1000', category: 'comida' })
    await api.createCommitment({ name: 'Luz', amount: '5000', due_date: '2026-08-10' })
    await api.updateProfile({ name: 'Persona', current_balance: '1000' })
    await api.createPurchaseSimulation({ purchase_name: 'Notebook', total_amount: '600000' })

    for (const [, options] of fetchMock.mock.calls) {
      const body = options.body ? JSON.parse(options.body) : {}
      expect(body).not.toHaveProperty('user_id')
      expect(body).not.toHaveProperty('userId')
      expect(body).not.toHaveProperty('profile_id')
    }
  })

  it('ninguna petición manda user_id en la URL', async () => {
    setAccessTokenProvider(async () => 'token-de-la-sesion')
    const api = await import('./api')

    await api.getProfile()
    await api.getTransactions()
    await api.getCommitments()
    await api.getDashboardSummary()
    await api.getSimulations()

    for (const [url] of fetchMock.mock.calls) {
      expect(url).not.toMatch(/user_id|userId|profile_id/)
    }
  })
})

describe('GET /auth/me', () => {
  it('devuelve la identidad que el backend leyó del token', async () => {
    const identity = { id: '3f2c1b9e-8a4d-4c1f-9b2e-5d6a7c8e9f01', email: 'persona@ejemplo.test' }
    fetchMock.mockResolvedValueOnce(jsonResponse(identity))
    setAccessTokenProvider(async () => 'token-de-la-sesion')

    await expect(getCurrentUser()).resolves.toEqual(identity)
    expect(fetchMock.mock.calls[0][0]).toContain('/api/v1/auth/me')
  })
})

describe('respuesta 429 (cuota diaria de IA)', () => {
  it('lanza un ApiError con el mensaje que manda el backend', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'Llegaste al límite de uso de IA por hoy.' }, { status: 429 }),
    )

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(429)
    expect(error.message).toBe('Llegaste al límite de uso de IA por hoy.')
  })

  it('usa un mensaje propio si el backend no manda detalle', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(null, { status: 429 }))

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error.message).toBe(AI_LIMIT_MESSAGE)
  })

  it('no reintenta: insistir no destraba nada hasta que cambie el día', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Sin cuota.' }, { status: 429 }))

    await chatCopilot('hola').catch(() => {})

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('un 429 no se confunde con una sesión caída', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Sin cuota.' }, { status: 429 }))

    await chatCopilot('hola').catch(() => {})

    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

/**
 * El backend responde 429 por dos motivos distintos y los distingue con `detail.code`.
 * Confundirlos no es cosmético: al de rate limit hay que esperarlo unos segundos, y al de
 * cuota, hasta mañana. Mostrar el mensaje equivocado manda a la persona a esperar un día
 * entero sin motivo, o a insistir contra un límite que no se destraba insistiendo.
 */
describe('respuesta 429 (rate limit)', () => {
  const rateLimitDetail = {
    code: 'rate_limit_exceeded',
    message: 'Estás haciendo demasiadas peticiones. Esperá unos segundos y volvé a intentar.',
  }

  it('muestra el mensaje de rate limit y no el de la cuota diaria', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: rateLimitDetail }, { status: 429 }))

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error.status).toBe(429)
    expect(error.message).toBe(rateLimitDetail.message)
    expect(error.message).not.toBe(AI_LIMIT_MESSAGE)
  })

  it('deja el `code` disponible para que la UI decida qué decir', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: rateLimitDetail }, { status: 429 }))

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error.detail.code).toBe('rate_limit_exceeded')
  })

  it('sin `message`, usa el texto de rate limit según el `code`', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: { code: 'rate_limit_exceeded' } }, { status: 429 }),
    )

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error.message).toBe(RATE_LIMIT_MESSAGE)
    expect(error.message).not.toBe(AI_LIMIT_MESSAGE)
  })

  it('tampoco se confunde con una sesión caída', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: rateLimitDetail }, { status: 429 }))

    await chatCopilot('hola').catch(() => {})

    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('lee el mensaje del detalle estructurado que manda el backend', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: 'daily_ai_limit_reached',
            message:
              'Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando ' +
              'las funciones manuales de Vector y volver a consultar mañana.',
            limit: 10,
            used: 10,
            remaining: 0,
            resets_at: '2026-07-30T00:00:00-03:00',
            reset_at: '2026-07-30T00:00:00-03:00',
            timezone: 'America/Argentina/Buenos_Aires',
          },
        },
        { status: 429 },
      ),
    )

    const error = await chatCopilot('hola').catch((failure) => failure)

    expect(error.message).toBe(
      'Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando las ' +
        'funciones manuales de Vector y volver a consultar mañana.',
    )
    // El objeto queda disponible para la UI, sin que cada componente vuelva a parsearlo.
    expect(error.detail).toMatchObject({
      code: 'daily_ai_limit_reached',
      limit: 10,
      remaining: 0,
      reset_at: '2026-07-30T00:00:00-03:00',
      timezone: 'America/Argentina/Buenos_Aires',
    })
  })
})

describe('401 y 429 no se comportan igual', () => {
  // Confundirlos mandaría al login a alguien que solo se quedó sin cuota.
  it('el 401 avisa de sesión caída y el 429 no', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Sesión inválida.' }, { status: 401 }))
    const sesion = await chatCopilot('hola').catch((failure) => failure)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { message: 'Sin cuota.' } }, { status: 429 }))
    const cuota = await chatCopilot('hola').catch((failure) => failure)

    // El contador de avisos no se movió con el 429.
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect(sesion.status).toBe(401)
    expect(cuota.status).toBe(429)
  })

  it('ninguno de los dos reintenta', async () => {
    setUnauthorizedHandler(vi.fn())

    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Sesión inválida.' }, { status: 401 }))
    await chatCopilot('hola').catch(() => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    fetchMock.mockClear()
    fetchMock.mockResolvedValue(jsonResponse({ detail: { message: 'Sin cuota.' } }, { status: 429 }))
    await chatCopilot('hola').catch(() => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('cuota diaria informada en las cabeceras', () => {
  it('registra lo que queda después de una llamada de IA', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ answer: 'hola' }),
      headers: {
        get: (name) =>
          ({
            'x-ai-daily-limit': '10',
            'x-ai-daily-remaining': '2',
            'x-ai-daily-warn-at': '3',
            'x-ai-daily-reset-at': '2026-07-30T00:00:00-03:00',
          })[name.toLowerCase()] ?? null,
      },
    })

    await chatCopilot('hola')

    expect(getAIUsage()).toMatchObject({
      remaining: 2,
      limit: 10,
      warning: true,
    })
  })

  it('una respuesta sin cabeceras de cuota no registra nada', async () => {
    await getProfile()

    expect(getAIUsage()).toBeNull()
  })
})

describe('respuesta 401', () => {
  it('lanza un ApiError con un mensaje claro de sesión', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Tu sesión no es válida o expiró.' }, { status: 401 }))

    const error = await getProfile().catch((failure) => failure)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(401)
    expect(error.message).toBe('Tu sesión no es válida o expiró.')
  })

  it('usa un mensaje propio si el backend no manda detalle', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(null, { status: 401 }))

    const error = await getProfile().catch((failure) => failure)

    expect(error.message).toBe(UNAUTHORIZED_MESSAGE)
  })

  it('avisa una sola vez a la capa de autenticación', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Sesión inválida.' }, { status: 401 }))

    await getProfile().catch(() => {})

    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('no reintenta la petición: un reintento con el mismo token sería otro 401', async () => {
    setAccessTokenProvider(async () => 'token-vencido')
    setUnauthorizedHandler(vi.fn())
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Sesión inválida.' }, { status: 401 }))

    await getProfile().catch(() => {})

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('un error del manejador no tapa el error de la petición', async () => {
    setUnauthorizedHandler(() => {
      throw new Error('falló el cierre de sesión')
    })
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'Sesión inválida.' }, { status: 401 }))

    const error = await getProfile().catch((failure) => failure)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(401)
  })

  it('los otros errores no avisan de sesión caída', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'No existe.' }, { status: 404 }))

    await getProfile().catch(() => {})

    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})
