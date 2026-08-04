/**
 * Estado de la cuota diaria de consultas inteligentes, tal como lo informa el backend.
 *
 * Es UNA sola cuota por cuenta y por día, compartida por todos los canales de IA (el
 * copiloto, la interpretación de movimientos y, más adelante, WhatsApp). Por eso acá hay
 * un único registro y no un mapa por operación.
 *
 * El límite lo decide y lo aplica el servidor: esto es solo el eco de lo último que dijo,
 * para poder avisar "te quedan 2 consultas" sin pedir nada extra. Nunca se calcula ni se
 * adivina el consumo del lado del navegador; si el frontend se equivocara, el backend
 * igual corta con 429.
 *
 * Los datos llegan en cabeceras de cada respuesta de IA, así que los cuerpos JSON quedaron
 * exactamente como estaban. `api.js` las lee en un solo lugar y llama a `recordUsage`.
 */

const HEADERS = {
  limit: 'x-ai-daily-limit',
  remaining: 'x-ai-daily-remaining',
  warnAt: 'x-ai-daily-warn-at',
  resetAt: 'x-ai-daily-reset-at',
}

let usage = null

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

  const limit = toPositiveInt(headers.get(HEADERS.limit))
  const remaining = toPositiveInt(headers.get(HEADERS.remaining))
  if (limit === null || remaining === null) {
    return
  }

  usage = {
    limit,
    remaining,
    warnAt: toPositiveInt(headers.get(HEADERS.warnAt)) ?? 0,
    resetAt: headers.get(HEADERS.resetAt) ?? null,
  }
}

/**
 * Último estado conocido de la cuota, o `null` si todavía no se usó IA hoy.
 *
 * `warning` es true solo mientras queda algo: con cero usos restantes ya no es un aviso,
 * es un bloqueo, y de eso se encarga el 429 con su propio mensaje.
 */
export function getAIUsage() {
  if (!usage) {
    return null
  }
  return {
    ...usage,
    warning: usage.remaining > 0 && usage.remaining <= usage.warnAt,
    exhausted: usage.remaining === 0,
  }
}

/** Olvida lo registrado. Se usa al cerrar sesión y en los tests. */
export function resetAIUsage() {
  usage = null
}
