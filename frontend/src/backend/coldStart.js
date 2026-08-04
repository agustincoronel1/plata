/**
 * Números y textos del arranque del backend.
 *
 * Viven en su propio módulo (sin componentes) por la misma razón que `AuthContext`: para
 * que el provider y el gate se puedan recargar en caliente, y para poder importarlos desde
 * los tests sin arrastrar un árbol de React.
 */

/**
 * Espera entre intentos, en milisegundos. Arranca corto (puede haber sido un hipo de red)
 * y se estira: un Render dormido no va a estar listo antes de ~30 segundos, así que
 * insistir cada segundo solo gasta pedidos. Pasado el último valor, se repite.
 */
export const RETRY_DELAYS_MS = [1500, 3000, 5000, 8000, 10000, 12000, 15000]

/**
 * Presupuesto total del arranque. Pasado este tiempo no se programan más intentos y se
 * pasa a `unavailable`. Con las esperas de arriba, el ciclo completo queda en el orden de
 * 75-85 segundos: suficiente para el arranque más lento que se ve en Render, y lo bastante
 * acotado como para no dejar a alguien mirando un spinner eterno.
 */
export const COLD_START_BUDGET_MS = 75000

/**
 * Cuánto tiene que haber estado disponible el backend para aceptar que una pantalla avise
 * que se cayó otra vez. Sin esto, una pantalla que falla siempre por otro motivo podría
 * reabrir el ciclo de arranque una y otra vez.
 */
export const MIN_RECHECK_MS = 30000

export const WAKING_MESSAGE =
  'Estamos iniciando el servidor de Plata. Puede tardar hasta un minuto la primera vez.'

export const UNAVAILABLE_MESSAGE =
  'No pudimos iniciar el servidor. Intentá nuevamente en unos minutos.'

/** Espera antes del intento número `attempt` (0 = el primero después del sondeo inicial). */
export function retryDelayFor(attempt) {
  return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)]
}
