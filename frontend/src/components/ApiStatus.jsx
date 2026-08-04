import { useBackendStatus } from '../backend/BackendStatusContext'

const LABELS = {
  checking: 'Verificando API',
  ready: 'API conectada',
  waking: 'Iniciando servidor',
  unavailable: 'API desconectada',
}

// El punto de color solo distingue tres situaciones; "arrancando" comparte el aspecto de
// "verificando" porque para quien mira es lo mismo: todavía no se sabe.
const DOT_STATE = {
  checking: 'loading',
  ready: 'online',
  waking: 'loading',
  unavailable: 'offline',
}

/**
 * Indicador discreto del estado del backend.
 *
 * No consulta nada por su cuenta: lee el estado que ya mantiene `BackendStatusProvider`.
 * Antes hacía su propio `fetch` a `/health` al montar, y con el gate de arranque eso serían
 * dos healthchecks en paralelo para responder la misma pregunta.
 */
export default function ApiStatus() {
  const { status } = useBackendStatus()

  return (
    <p className="api-status" data-state={DOT_STATE[status]} role="status" aria-live="polite">
      <span className="api-status__dot" aria-hidden="true" />
      {LABELS[status]}
    </p>
  )
}
