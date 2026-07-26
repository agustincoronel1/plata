import { useEffect, useState } from 'react'

import { fetchApiHealth } from '../services/api'

const LABELS = {
  loading: 'Verificando API',
  online: 'API conectada',
  offline: 'API desconectada',
}

/**
 * Indicador discreto del estado del backend.
 *
 * Una única consulta al montar: sin polling ni reintentos automáticos.
 */
export default function ApiStatus() {
  const [state, setState] = useState('loading')

  useEffect(() => {
    let active = true

    fetchApiHealth().then((result) => {
      if (active) {
        setState(result.ok ? 'online' : 'offline')
      }
    })

    return () => {
      active = false
    }
  }, [])

  return (
    <p className="api-status" data-state={state} role="status" aria-live="polite">
      <span className="api-status__dot" aria-hidden="true" />
      {LABELS[state]}
    </p>
  )
}
