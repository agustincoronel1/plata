import { recordUsageFromHeaders } from './aiUsage'
import { getAccessToken, notifyUnauthorized } from './authToken'

const FALLBACK_API_URL = 'http://127.0.0.1:8000'

export const API_URL = import.meta.env.VITE_API_URL || FALLBACK_API_URL

const API_V1 = `${API_URL}/api/v1`

/**
 * Timeout de las consultas normales: solo tocan PostgreSQL y responden en milisegundos.
 * Si tardan 5 segundos, algo está mal.
 */
export const DEFAULT_TIMEOUT_MS = 5000

/**
 * Timeout de las operaciones que llaman al modelo real. Una interpretación o una respuesta
 * del copiloto puede tardar más de 13 segundos: con el timeout corto el navegador cancelaba
 * una petición que el backend estaba resolviendo bien.
 */
export const AI_TIMEOUT_MS = 60000

/**
 * Error unificado de la API. La UI solo necesita `message` (texto ya comprensible) y,
 * cuando corresponde, `fieldErrors` para mostrar el problema junto al input.
 *
 * `status` 0 significa que no hubo respuesta. Hay dos casos bien distintos y no se mezclan:
 * `timeout` true = la petición llegó al backend pero tardó de más; `isOffline` true = no
 * hubo conexión (red caída, backend apagado o CORS).
 */
export class ApiError extends Error {
  constructor(message, { status = 0, fieldErrors = null, timeout = false, detail = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
    this.timeout = timeout
    // Detalle estructurado, cuando el backend manda un objeto en lugar de un texto (hoy,
    // solo el 429 de cuota diaria). `null` en el resto de los errores.
    this.detail = detail
  }

  get isOffline() {
    return this.status === 0 && !this.timeout
  }
}

const OFFLINE_MESSAGE = 'No pudimos conectar con el servidor. Revisá que el backend esté activo.'
const UNEXPECTED_MESSAGE = 'Ocurrió un error inesperado. Intentá de nuevo.'
const AI_TIMEOUT_MESSAGE = 'La IA tardó más de lo esperado. Intentá nuevamente.'
const TIMEOUT_MESSAGE = 'El servidor tardó más de lo esperado. Intentá nuevamente.'
export const UNAUTHORIZED_MESSAGE = 'Tu sesión expiró. Iniciá sesión de nuevo.'
/**
 * Respaldo del texto del 429. El backend manda el suyo (con el límite real configurado) y
 * ese es el que se muestra; esto cubre el caso de que el detalle no llegue. Nunca se
 * muestra "Algo salió mal" para un límite alcanzado.
 */
export const AI_LIMIT_MESSAGE =
  'Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando las ' +
  'funciones manuales de Plata y volver a consultar mañana.'

/**
 * Texto del 429 de cuota diaria. El backend manda `detail` como objeto con `message`; si
 * por algún motivo no llega, se usa un texto propio en vez de mostrar algo técnico.
 */
function limitMessage(detail) {
  if (typeof detail === 'string') {
    return detail
  }
  if (detail && typeof detail.message === 'string') {
    return detail.message
  }
  return AI_LIMIT_MESSAGE
}

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
 * cualquier error a un ApiError con mensaje comprensible.
 *
 * La sesión viaja acá y en ningún otro lado: ningún componente arma el header
 * `Authorization` a mano.
 *
 * No hay reintentos automáticos: una petición cancelada por el navegador puede seguir
 * procesándose en el backend, y reintentarla gastaría otra llamada o crearía un segundo
 * borrador.
 */
async function request(path, { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  // Se pide justo antes de salir, no se guarda, así siempre se usa el token vigente. Va
  // antes de arrancar el reloj para que leer la sesión no le coma tiempo al timeout.
  const accessToken = await getAccessToken()

  const controller = new AbortController()
  // Distinguir el timeout del error de red: el AbortError lo disparamos nosotros.
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  let response
  try {
    response = await fetch(`${API_V1}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
  } catch (error) {
    if (timedOut || error?.name === 'AbortError') {
      const message = timeoutMs >= AI_TIMEOUT_MS ? AI_TIMEOUT_MESSAGE : TIMEOUT_MESSAGE
      throw new ApiError(message, { status: 0, timeout: true })
    }
    throw new ApiError(OFFLINE_MESSAGE, { status: 0 })
  } finally {
    clearTimeout(timeout)
  }

  // Las respuestas de IA informan cuánta cuota diaria queda. Se lee acá, en un solo lugar:
  // ningún componente toca cabeceras y los cuerpos JSON siguen igual que siempre.
  recordUsageFromHeaders(response.headers)

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

  if (response.status === 401) {
    // El backend ya no reconoce la sesión. Se avisa una sola vez —quien escucha cierra la
    // sesión y la aplicación vuelve al login— y se corta: reintentar con el mismo token
    // daría otro 401 y armaría un ciclo.
    notifyUnauthorized()
    const detail = typeof payload?.detail === 'string' ? payload.detail : UNAUTHORIZED_MESSAGE
    throw new ApiError(detail, { status: 401 })
  }

  if (response.status === 429) {
    // Cuota diaria de IA agotada. Un 429 no es una sesión caída: no se avisa a la capa de
    // autenticación, así que no cierra sesión ni manda al login.
    throw new ApiError(limitMessage(payload?.detail), {
      status: 429,
      // El objeto entero (kind, resets_at, code) queda disponible para la UI.
      detail: typeof payload?.detail === 'object' ? payload.detail : null,
    })
  }

  if (response.status === 422) {
    const { message, fieldErrors } = parseValidationError(payload?.detail)
    throw new ApiError(message, { status: 422, fieldErrors })
  }

  const detail = typeof payload?.detail === 'string' ? payload.detail : UNEXPECTED_MESSAGE
  throw new ApiError(detail, { status: response.status })
}

// ---------- Autenticación ----------

/** Identidad que el backend leyó del JWT: `{ id, email }`. */
export function getCurrentUser() {
  return request('/auth/me')
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
 * Interpreta una frase y devuelve un borrador editable. No guarda nada: el saldo no cambia
 * hasta confirmar. Llama al modelo, así que usa el timeout largo.
 */
export function parseAITransaction(text) {
  return request('/ai/transactions/parse', {
    method: 'POST',
    body: { text },
    timeoutMs: AI_TIMEOUT_MS,
  })
}

/**
 * Confirma un borrador (con correcciones opcionales) y recién ahí registra el movimiento.
 * No vuelve a llamar al modelo, así que conserva el timeout corto.
 */
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

/** Corre el grafo del agente contra el modelo real: usa el timeout largo. */
export function chatCopilot(message, conversationId = null) {
  return request('/ai/chat', {
    method: 'POST',
    body: { message, conversation_id: conversationId },
    timeoutMs: AI_TIMEOUT_MS,
  })
}

/**
 * Aprobar/rechazar reanuda el grafo en el nodo `apply_write`, que va derecho a END: escribe
 * (o descarta) en PostgreSQL sin volver a llamar al modelo. Timeout corto.
 */
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

// ---------- Disponibilidad del backend ----------

/**
 * Cuánto se espera al `/health` antes de dar por perdido un intento.
 *
 * Más generoso que `DEFAULT_TIMEOUT_MS` a propósito: en Render con plan gratuito la
 * primera petición después de un rato queda esperando a que el servicio arranque, y
 * cortarla a los 5 segundos convertiría un arranque normal en un "no se pudo conectar".
 */
export const HEALTH_TIMEOUT_MS = 8000

/**
 * Códigos que devuelve un proxy cuando todavía no hay backend detrás. Con Render dormido
 * son exactamente estos, y significan "esperá", no "está roto".
 */
const WAKING_STATUSES = new Set([502, 503, 504])

/**
 * Healthcheck de la API, no el de la base: responde 200 mientras el backend esté vivo,
 * aunque PostgreSQL esté detenido. Resuelve siempre; nunca lanza.
 *
 * Devuelve `{ ok, version, status, waking }`. `waking` distingue "el servidor todavía no
 * está levantado" (timeout, red caída, 502/503/504) de "el servidor contestó algo que está
 * mal" (un 500, un 404): lo primero se reintenta con el mensaje de arranque, lo segundo es
 * un error de verdad y conserva el mensaje genérico.
 *
 * `signal` permite cancelar desde afuera al desmontar el componente que la llamó; el
 * timeout interno sigue existiendo igual, así que nunca queda una petición colgada.
 */
export async function fetchApiHealth({ signal, timeoutMs = HEALTH_TIMEOUT_MS } = {}) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort)
  const timeout = setTimeout(abort, timeoutMs)

  try {
    const response = await fetch(`${API_URL}/health`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })

    if (!response.ok) {
      return { ok: false, status: response.status, waking: WAKING_STATUSES.has(response.status) }
    }

    const body = await response.json().catch(() => null)
    if (body?.status !== 'ok') {
      // Respondió 200 pero no es el healthcheck de Plata (un proxy, una página de error):
      // tampoco es "arrancando".
      return { ok: false, status: response.status, waking: false }
    }
    return { ok: true, version: body?.version, status: response.status, waking: false }
  } catch {
    // Red caída, backend apagado, CORS o timeout. Con Render gratuito, el caso normal es
    // que el servicio esté durmiendo, así que se trata como arranque y se reintenta.
    return { ok: false, status: 0, waking: true }
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}
