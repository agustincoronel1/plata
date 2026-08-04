import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchApiHealth } from '../services/api'
import { BackendStatusContext } from './BackendStatusContext'
import { COLD_START_BUDGET_MS, MIN_RECHECK_MS, retryDelayFor } from './coldStart'

/**
 * Única fuente de verdad sobre si el backend está disponible.
 *
 * Existe por el plan gratuito de Render: cuando el servicio está dormido, la primera
 * petición tarda hasta cerca de un minuto en responder. Sin esto, cada pantalla veía un
 * error de red y mostraba "No pudimos conectar con el servidor" o "Algo salió mal", que
 * describen mal lo que pasa: el servidor no está roto, se está encendiendo.
 *
 * Toda la lógica vive acá y en ningún otro lado. Las pantallas no consultan `/health`, no
 * reintentan y no tienen temporizadores: preguntan el estado y se comportan según él.
 *
 * Lo que este provider NO decide:
 *
 * - Un 401 es sesión vencida y lo resuelve el flujo de autenticación de siempre.
 * - Un 429 es el límite diario de consultas inteligentes y tiene su propio mensaje.
 * - Un 500 es un error real y conserva el mensaje genérico.
 *
 * `/health` es público y no lleva token, así que ninguno de esos tres códigos puede llegar
 * desde acá. La única señal que se interpreta como "arrancando" es la que informa
 * `fetchApiHealth` con `waking: true`.
 *
 * Nunca hay dos sondeos en paralelo: cada ciclo tiene una generación, y un sondeo de una
 * generación vieja no toca el estado ni programa nada.
 */

export default function BackendStatusProvider({ children }) {
  const [status, setStatus] = useState('checking')
  // Intentos fallidos del ciclo actual. Es lo que se le muestra a la persona.
  const [attempts, setAttempts] = useState(0)

  // Todo lo cancelable, junto y en refs: el temporizador del próximo intento y la petición
  // en vuelo. `runId` identifica el ciclo actual; lo que venga de otro ciclo se descarta.
  const timerRef = useRef(null)
  const controllerRef = useRef(null)
  const runIdRef = useRef(0)
  const startedAtRef = useRef(0)
  const readySinceRef = useRef(0)

  const cancelPending = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    controllerRef.current?.abort()
    controllerRef.current = null
  }, [])

  const probe = useCallback(async function probe(runId, attempt) {
    if (runId !== runIdRef.current) return

    const controller = new AbortController()
    controllerRef.current = controller
    const result = await fetchApiHealth({ signal: controller.signal })

    // Desmontado, reiniciado o cancelado mientras se esperaba: no se toca nada.
    if (runId !== runIdRef.current) return

    if (result.ok) {
      readySinceRef.current = Date.now()
      setAttempts(0)
      setStatus('ready')
      return
    }

    const failed = attempt + 1
    setAttempts(failed)

    // Un error que no es de arranque (un 500, un 404) no se reintenta solo: insistir no lo
    // va a arreglar y taparía un problema real detrás del mensaje de "iniciando".
    if (!result.waking) {
      setStatus('unavailable')
      return
    }

    if (Date.now() - startedAtRef.current >= COLD_START_BUDGET_MS) {
      setStatus('unavailable')
      return
    }

    setStatus('waking')
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      probe(runId, failed)
    }, retryDelayFor(attempt))
  }, [])

  const start = useCallback(() => {
    cancelPending()
    const runId = runIdRef.current + 1
    runIdRef.current = runId
    startedAtRef.current = Date.now()
    setAttempts(0)
    setStatus('checking')
    probe(runId, 0)
  }, [cancelPending, probe])

  useEffect(() => {
    start()

    return () => {
      // Nada sobrevive al desmontaje: se invalida el ciclo, se limpia el temporizador y se
      // aborta la petición en vuelo.
      runIdRef.current += 1
      cancelPending()
    }
  }, [start, cancelPending])

  /** "Reintentar ahora": reinicia el ciclo completo, con su presupuesto de tiempo nuevo. */
  const retryNow = useCallback(() => {
    start()
  }, [start])

  /**
   * Aviso desde una pantalla de que el backend dejó de responder (una petición que se fue
   * en timeout o sin conexión). Reabre el ciclo de arranque, pero solo si el backend venía
   * estable: así una pantalla que falla por otra cosa no puede dejar el ciclo girando.
   */
  const reportUnavailable = useCallback(() => {
    if (status !== 'ready') return
    if (Date.now() - readySinceRef.current < MIN_RECHECK_MS) return
    start()
  }, [status, start])

  const value = useMemo(
    () => ({ status, attempts, retryNow, reportUnavailable }),
    [status, attempts, retryNow, reportUnavailable],
  )

  return <BackendStatusContext.Provider value={value}>{children}</BackendStatusContext.Provider>
}
