const FALLBACK_API_URL = 'http://127.0.0.1:8000'

export const API_URL = import.meta.env.VITE_API_URL || FALLBACK_API_URL

const API_V1 = `${API_URL}/api/v1`
const TIMEOUT_MS = 5000

/**
 * Error unificado de la API. La UI solo necesita `message` (texto ya comprensible) y,
 * cuando corresponde, `fieldErrors` para mostrar el problema junto al input.
 *
 * `status` 0 significa que no hubo respuesta: red caída, backend apagado, CORS o timeout.
 */
export class ApiError extends Error {
  constructor(message, { status = 0, fieldErrors = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
  }

  get isOffline() {
    return this.status === 0
  }
}

const OFFLINE_MESSAGE = 'No pudimos conectar con el servidor. Revisá que el backend esté activo.'
const UNEXPECTED_MESSAGE = 'Ocurrió un error inesperado. Intentá de nuevo.'

/**
 * Convierte el `detail` de un 422 de Pydantic en algo mostrable.
 *
 * Devuelve `{ message, fieldErrors }`: un resumen general y un mapa campo -> mensaje,
 * para poder describir cada input con aria-describedby.
 */
function parseValidationError(detail) {
  if (!Array.isArray(detail)) {
    return { message: typeof detail === 'string' ? detail : UNEXPECTED_MESSAGE, fieldErrors: null }
  }

  const fieldErrors = {}
  for (const item of detail) {
    // loc suele ser ["body", "campo"]; tomamos el último tramo como nombre del campo.
    const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null
    if (field && !(field in fieldErrors)) {
      fieldErrors[field] = item?.msg ?? 'Valor inválido.'
    }
  }

  const message = 'Revisá los datos del formulario.'
  return { message, fieldErrors: Object.keys(fieldErrors).length ? fieldErrors : null }
}

/**
 * Wrapper único sobre fetch: arma la URL, serializa JSON, aplica timeout y traduce
 * cualquier error a un ApiError con mensaje comprensible. Nunca deja escapar detalles
 * técnicos del backend.
 *
 * - 204: resuelve a `null` (sin body).
 * - 2xx: resuelve al JSON parseado.
 * - 422: ApiError con fieldErrors.
 * - 404 / otros 4xx-5xx: ApiError con el `detail` del backend o un mensaje genérico.
 */
async function request(path, { method = 'GET', body } = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS)

  let response
  try {
    response = await fetch(`${API_V1}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
  } catch {
    throw new ApiError(OFFLINE_MESSAGE, { status: 0 })
  } finally {
    clearTimeout(timeout)
  }

  if (response.status === 204) {
    return null
  }

  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (response.ok) {
    return payload
  }

  if (response.status === 422) {
    const { message, fieldErrors } = parseValidationError(payload?.detail)
    throw new ApiError(message, { status: 422, fieldErrors })
  }

  const detail = typeof payload?.detail === 'string' ? payload.detail : UNEXPECTED_MESSAGE
  throw new ApiError(detail, { status: response.status })
}

// ---------- Perfil ----------

export function getProfile() {
  return request('/profile')
}

export function updateProfile(data) {
  return request('/profile', { method: 'PUT', body: data })
}

// ---------- Movimientos ----------

export function getTransactions() {
  return request('/transactions')
}

export function createTransaction(data) {
  return request('/transactions', { method: 'POST', body: data })
}

export function updateTransaction(id, data) {
  return request(`/transactions/${id}`, { method: 'PATCH', body: data })
}

export function deleteTransaction(id) {
  return request(`/transactions/${id}`, { method: 'DELETE' })
}

// ---------- Compromisos ----------

export function getCommitments() {
  return request('/commitments')
}

export function createCommitment(data) {
  return request('/commitments', { method: 'POST', body: data })
}

export function updateCommitment(id, data) {
  return request(`/commitments/${id}`, { method: 'PATCH', body: data })
}

export function deleteCommitment(id) {
  return request(`/commitments/${id}`, { method: 'DELETE' })
}

// ---------- Dashboard (motor financiero) ----------

export function getDashboardSummary() {
  return request('/dashboard/summary')
}

// ---------- Simulaciones de compra ----------

export function createPurchaseSimulation(data) {
  return request('/simulations/purchase', { method: 'POST', body: data })
}

export function getSimulations() {
  return request('/simulations')
}

// ---------- Registro asistido por IA (parse -> borrador -> confirmación) ----------

/**
 * Interpreta una frase en lenguaje natural y devuelve un BORRADOR editable. No guarda nada:
 * el saldo no cambia hasta confirmar. Nunca se envía ni se recibe la API key.
 */
export function parseAITransaction(text) {
  return request('/ai/transactions/parse', { method: 'POST', body: { text } })
}

/** Confirma un borrador (con correcciones opcionales) y recién ahí registra el movimiento. */
export function confirmAITransaction(draftId, { corrections = null } = {}) {
  return request(`/ai/transactions/${draftId}/confirm`, {
    method: 'POST',
    body: { confirmed: true, corrections },
  })
}

/** Descarta un borrador. No crea movimiento ni modifica el saldo. */
export function rejectAITransaction(draftId) {
  return request(`/ai/transactions/${draftId}/reject`, { method: 'POST' })
}

// ---------- Copiloto financiero ----------

export function chatCopilot(message, conversationId = null) {
  return request('/ai/chat', { method: 'POST', body: { message, conversation_id: conversationId } })
}

export function approveCopilotAction(conversationId, actionId) {
  return request(`/ai/conversations/${conversationId}/approve`, {
    method: 'POST',
    body: { action_id: actionId },
  })
}

export function rejectCopilotAction(conversationId, actionId) {
  return request(`/ai/conversations/${conversationId}/reject`, {
    method: 'POST',
    body: { action_id: actionId },
  })
}

/**
 * Consulta GET /health. Es el healthcheck de la API, no el de la base:
 * responde 200 mientras el backend esté vivo, aunque PostgreSQL esté detenido.
 *
 * Resuelve siempre; nunca lanza. Los detalles del error quedan fuera de la UI.
 */
export async function fetchApiHealth() {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS)

  try {
    const response = await fetch(`${API_URL}/health`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })

    if (!response.ok) {
      return { ok: false }
    }

    const body = await response.json()
    return { ok: body?.status === 'ok', version: body?.version }
  } catch {
    // Red caída, backend apagado, CORS o timeout: para la UI son lo mismo.
    return { ok: false }
  } finally {
    clearTimeout(timeout)
  }
}
