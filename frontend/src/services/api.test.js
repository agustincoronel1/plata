import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AI_TIMEOUT_MS,
  ApiError,
  DEFAULT_TIMEOUT_MS,
  approveCopilotAction,
  chatCopilot,
  confirmAITransaction,
  getProfile,
  parseAITransaction,
} from './api'
import { makeChatResponse, makeParseDraft } from '../test/fixtures'

const OFFLINE_MESSAGE = 'No pudimos conectar con el servidor. Revisá que el backend esté activo.'
const AI_TIMEOUT_MESSAGE = 'La IA tardó más de lo esperado. Intentá nuevamente.'
const TIMEOUT_MESSAGE = 'El servidor tardó más de lo esperado. Intentá nuevamente.'

function abortError() {
  const error = new Error('The operation was aborted.')
  error.name = 'AbortError'
  return error
}

function jsonResponse(payload, { status = 200 } = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload }
}

/** fetch que nunca responde: solo termina si el AbortController lo cancela. */
function neverRespondingFetch() {
  return vi.fn(
    (_url, options) =>
      new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(abortError()))
      }),
  )
}

/** fetch que responde recién después de `delayMs` (timers falsos), y respeta el abort. */
function slowFetch(delayMs, payload) {
  return vi.fn(
    (_url, options) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve(jsonResponse(payload)), delayMs)
        options.signal.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(abortError())
        })
      }),
  )
}

/** Evita rechazos sin manejar mientras adelantamos el reloj: devuelve el error, no lanza. */
function capture(promise) {
  return promise.then(
    (value) => ({ value, error: null }),
    (error) => ({ value: null, error }),
  )
}

function signalOf(fetchMock, call = 0) {
  return fetchMock.mock.calls[call][1].signal
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('timeouts separados', () => {
  it('las peticiones normales se cancelan a los 5000 ms', async () => {
    expect(DEFAULT_TIMEOUT_MS).toBe(5000)
    const fetchMock = neverRespondingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const settled = capture(getProfile())
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS - 1)
    expect(signalOf(fetchMock).aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(1)
    expect(signalOf(fetchMock).aborted).toBe(true)

    const { error } = await settled
    expect(error).toBeInstanceOf(ApiError)
    expect(error.timeout).toBe(true)
  })

  it('parseAITransaction usa 60000 ms', async () => {
    expect(AI_TIMEOUT_MS).toBe(60000)
    const fetchMock = neverRespondingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const settled = capture(parseAITransaction('Gasté 25 lucas en nafta'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS - 1)
    expect(signalOf(fetchMock).aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(1)
    expect(signalOf(fetchMock).aborted).toBe(true)

    const { error } = await settled
    expect(error.timeout).toBe(true)
  })

  it('chatCopilot usa 60000 ms', async () => {
    const fetchMock = neverRespondingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const settled = capture(chatCopilot('¿Cuánto puedo gastar hoy?'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS - 1)
    expect(signalOf(fetchMock).aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(1)
    expect(signalOf(fetchMock).aborted).toBe(true)

    const { error } = await settled
    expect(error.timeout).toBe(true)
  })

  it('confirmar y aprobar solo tocan PostgreSQL: conservan el timeout corto', async () => {
    const fetchMock = neverRespondingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const confirming = capture(confirmAITransaction('draft-1'))
    const approving = capture(approveCopilotAction('conv-1', 'action-1'))

    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS)
    expect(signalOf(fetchMock, 0).aborted).toBe(true)
    expect(signalOf(fetchMock, 1).aborted).toBe(true)

    expect((await confirming).error.timeout).toBe(true)
    expect((await approving).error.timeout).toBe(true)
  })

  it('acepta una respuesta de IA que tarda 13 segundos', async () => {
    const draft = makeParseDraft()
    const fetchMock = slowFetch(13100, draft)
    vi.stubGlobal('fetch', fetchMock)

    const settled = capture(parseAITransaction('Gasté 25 lucas ayer en nafta con débito'))
    await vi.advanceTimersByTimeAsync(13100)

    const { value, error } = await settled
    expect(error).toBeNull()
    expect(value).toEqual(draft)
    expect(signalOf(fetchMock).aborted).toBe(false)
  })

  it('acepta una respuesta del copiloto que tarda 13 segundos', async () => {
    const chat = makeChatResponse()
    vi.stubGlobal('fetch', slowFetch(13100, chat))

    const settled = capture(chatCopilot('¿Cuánto puedo gastar hoy?'))
    await vi.advanceTimersByTimeAsync(13100)

    expect((await settled).value).toEqual(chat)
  })
})

describe('timeout vs error de red vs error del backend', () => {
  it('un timeout de IA no muestra el mensaje de backend apagado', async () => {
    vi.stubGlobal('fetch', neverRespondingFetch())

    const settled = capture(parseAITransaction('Gasté 25 lucas'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS)

    const { error } = await settled
    expect(error.message).toBe(AI_TIMEOUT_MESSAGE)
    expect(error.message).not.toBe(OFFLINE_MESSAGE)
    expect(error.isOffline).toBe(false)
  })

  it('un AbortError ajeno al timeout tampoco se confunde con backend apagado', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(abortError())))

    const { error } = await capture(parseAITransaction('Gasté 25 lucas'))
    expect(error.timeout).toBe(true)
    expect(error.message).toBe(AI_TIMEOUT_MESSAGE)
  })

  it('un timeout de una consulta normal avisa que tardó, no que el backend está caído', async () => {
    vi.stubGlobal('fetch', neverRespondingFetch())

    const settled = capture(getProfile())
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS)

    const { error } = await settled
    expect(error.message).toBe(TIMEOUT_MESSAGE)
    expect(error.isOffline).toBe(false)
  })

  it('un error de red real sí muestra el mensaje de backend apagado', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))

    const { error } = await capture(parseAITransaction('Gasté 25 lucas'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe(OFFLINE_MESSAGE)
    expect(error.isOffline).toBe(true)
    expect(error.timeout).toBe(false)
  })

  it('un error HTTP del backend muestra el mensaje seguro que devolvió la API', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ detail: 'El servicio de IA no está disponible.' }, { status: 503 }),
        ),
      ),
    )

    const { error } = await capture(parseAITransaction('Gasté 25 lucas'))
    expect(error.status).toBe(503)
    expect(error.message).toBe('El servicio de IA no está disponible.')
    expect(error.timeout).toBe(false)
  })
})

describe('sin reintentos automáticos', () => {
  it('no reintenta después de un timeout', async () => {
    const fetchMock = neverRespondingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const settled = capture(parseAITransaction('Gasté 25 lucas'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS * 3)
    await settled

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('no reintenta después de un error de red', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))
    vi.stubGlobal('fetch', fetchMock)

    await capture(chatCopilot('hola'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS)

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('no reintenta después de un error HTTP', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Ups.' }, { status: 500 })))
    vi.stubGlobal('fetch', fetchMock)

    await capture(chatCopilot('hola'))
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS)

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
