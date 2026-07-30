/**
 * Estado de la cuota diaria de IA, tal como lo informa el backend.
 *
 * Los límites los decide y los aplica el servidor: esto es solo el eco de lo último que
 * dijo, para poder avisar "te quedan 2 consultas" sin pedir nada extra. Nunca se calcula
 * ni se adivina el consumo del lado del navegador; si el frontend se equivocara, el
 * backend igual corta con 429.
 *
 * Los datos llegan en cabeceras de cada respuesta de IA, así que los cuerpos JSON quedaron
 * exactamente como estaban. `api.js` las lee en un solo lugar y llama a `recordUsage`.
 */

export const AI_USAGE_KINDS = {
  copilotChat: 'copilot_chat',
  transactionParse: 'transaction_parse',
}

const HEADERS = {
  kind: 'x-ai-daily-kind',
  limit: 'x-ai-daily-limit',
  remaining: 'x-ai-daily-remaining',
  warnAt: 'x-ai-daily-warn-at',
}

const usage = new Map()

function toPositiveInt(value) {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

/**
 * Guarda el estado que viene en las cabeceras de una respuesta.
 *
 * Tolera que no estén: los endpoints sin cuota no las mandan, y una respuesta a la que el
 * navegador no le deja leer cabeceras (CORS mal configurado) simplemente no actualiza nada
 * en vez de romper la llamada.
 */
export function recordUsageFromHeaders(headers) {
  if (!headers || typeof headers.get !== 'function') {
    return
  }

  const kind = headers.get(HEADERS.kind)
  const limit = toPositiveInt(headers.get(HEADERS.limit))
  const remaining = toPositiveInt(headers.get(HEADERS.remaining))
  if (!kind || limit === null || remaining === null) {
    return
  }

  usage.set(kind, {
    kind,
    limit,
    remaining,
    warnAt: toPositiveInt(headers.get(HEADERS.warnAt)) ?? 0,
  })
}

/**
 * Último estado conocido de una cuota, o `null` si todavía no se usó esa operación hoy.
 *
 * `warning` es true solo mientras queda algo: con cero usos restantes ya no es un aviso,
 * es un bloqueo, y de eso se encarga el 429 con su propio mensaje.
 */
export function getAIUsage(kind) {
  const current = usage.get(kind)
  if (!current) {
    return null
  }
  return {
    ...current,
    warning: current.remaining > 0 && current.remaining <= current.warnAt,
    exhausted: current.remaining === 0,
  }
}

/** Olvida lo registrado. Se usa al cerrar sesión y en los tests. */
export function resetAIUsage() {
  usage.clear()
}
