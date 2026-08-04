import { createContext, useContext } from 'react'

/**
 * Contexto de disponibilidad del backend. Vive en su propio módulo (sin componentes) para
 * que el provider se pueda recargar en caliente sin perder el contexto.
 *
 * El valor lo publica BackendStatusProvider:
 * `{ status, attempts, retryNow, reportUnavailable }`.
 *
 * Estados posibles:
 *
 * - `checking`: primer sondeo en curso. Todavía no se sabe nada.
 * - `ready`: `/health` respondió 200. Se puede pedir cualquier cosa.
 * - `waking`: hubo al menos un fallo compatible con un servidor arrancando (timeout, red
 *   caída, 502/503/504). Se sigue reintentando solo.
 * - `unavailable`: se agotaron los reintentos, o el backend contestó un error que no es
 *   arranque (un 500). Solo se sale de acá con el botón de reintento.
 */
export const BackendStatusContext = createContext(null)

/**
 * Valor neutro para quien se monte sin provider (tests que renderizan una pantalla
 * aislada). Se asume el backend disponible: sin provider no hay nadie que sepa lo
 * contrario, y bloquear la pantalla sería peor que dejarla pedir datos.
 */
const STANDALONE = {
  status: 'ready',
  attempts: 0,
  retryNow: () => {},
  reportUnavailable: () => {},
}

/** Estado del backend desde cualquier componente. */
export function useBackendStatus() {
  return useContext(BackendStatusContext) ?? STANDALONE
}
