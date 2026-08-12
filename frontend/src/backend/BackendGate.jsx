import ApiStatus from '../components/ApiStatus'
import BrandMark from '../components/BrandMark'
import Icon from '../components/Icon'
import LoadingSkeleton from '../components/LoadingSkeleton'
import { useBackendStatus } from './BackendStatusContext'
import { UNAVAILABLE_MESSAGE, WAKING_MESSAGE } from './coldStart'

/**
 * Puerta de entrada a todo lo que necesita el backend.
 *
 * Mientras `/health` no conteste, los hijos no se montan: así no se disparan a la vez las
 * cinco consultas del dashboard contra un servidor que todavía está arrancando. Cuando
 * contesta, se montan y piden sus datos solos — no hay que recargar la página.
 *
 * Las tres pantallas que puede mostrar usan el mismo `boot` que el resto de Vector: es el
 * mismo esqueleto de carga de siempre, no una pantalla nueva.
 */

function BootFrame({ children, onSignOut }) {
  return (
    <div className="boot">
      <header className="boot__top">
        <BrandMark />
        <div className="boot__actions">
          <ApiStatus />
          {onSignOut && (
            <button
              type="button"
              className="topbar__signout"
              onClick={onSignOut}
              aria-label="Cerrar sesión"
              title="Cerrar sesión"
            >
              <Icon name="logout" />
            </button>
          )}
        </div>
      </header>

      <main className="boot__main">{children}</main>
    </div>
  )
}

export default function BackendGate({ children, onSignOut }) {
  const { status, attempts, retryNow } = useBackendStatus()

  if (status === 'ready') {
    return children
  }

  if (status === 'unavailable') {
    return (
      <BootFrame onSignOut={onSignOut}>
        <section className="state state--warning">
          <span className="state__icon">
            <Icon name="alert" />
          </span>
          <h2>No pudimos iniciar el servidor</h2>
          <p>{UNAVAILABLE_MESSAGE}</p>
          <button type="button" className="btn btn--primary" onClick={retryNow}>
            Reintentar ahora
          </button>
        </section>
      </BootFrame>
    )
  }

  if (status === 'waking') {
    return (
      <BootFrame onSignOut={onSignOut}>
        <section className="state state--waking">
          <LoadingSkeleton />
          <h2>Estamos iniciando el servidor</h2>
          <p role="status" aria-live="polite">
            {WAKING_MESSAGE}
          </p>
          <p className="state__detail">
            {attempts === 1 ? 'Reintento 1…' : `Reintentos: ${attempts}`}
          </p>
          <button type="button" className="btn btn--primary" onClick={retryNow}>
            Reintentar ahora
          </button>
        </section>
      </BootFrame>
    )
  }

  // `checking`: primer sondeo. Todavía no hay motivo para hablar de arranque lento, así
  // que se muestra el mismo esqueleto de carga que cualquier otra espera corta.
  return (
    <BootFrame onSignOut={onSignOut}>
      <LoadingSkeleton />
      <p className="loading-status" role="status" aria-live="polite">
        Conectando con Vector…
      </p>
    </BootFrame>
  )
}
