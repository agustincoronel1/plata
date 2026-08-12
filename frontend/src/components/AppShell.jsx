import ApiStatus from './ApiStatus'
import BrandMark from './BrandMark'
import Icon from './Icon'

/**
 * Estructura de la aplicación: barra lateral en desktop, header compacto, contenido y
 * navegación inferior en mobile.
 *
 * Vector es una sola pantalla con secciones, así que la navegación no cambia de ruta: lleva
 * a la sección correspondiente y marca cuál está activa. "Perfil" abre el formulario de
 * situación que ya existe.
 */

const SECTIONS = [
  { id: 'inicio', label: 'Inicio', icon: 'home' },
  { id: 'movimientos', label: 'Movimientos', icon: 'receipt' },
  { id: 'compromisos', label: 'Compromisos', icon: 'calendar' },
  { id: 'simulaciones', label: 'Simulador', icon: 'calculator' },
  { id: 'copiloto', label: 'Copiloto', icon: 'sparkle' },
]

// En mobile entran cinco destinos: cuatro secciones y el acceso al perfil.
const MOBILE_SECTIONS = SECTIONS.filter((item) => item.id !== 'compromisos')

function initialOf(name) {
  const clean = (name ?? '').trim()
  return clean ? clean[0].toUpperCase() : 'P'
}

function firstNameOf(name) {
  const clean = (name ?? '').trim()
  return clean ? clean.split(' ')[0] : null
}

export default function AppShell({
  userName,
  active,
  onNavigate,
  onOpenProfile,
  onOpenCopilot,
  onSignOut,
  copilotOpen = false,
  footer,
  children,
}) {
  const firstName = firstNameOf(userName)

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar__inner">
          <BrandMark />

          <nav className="sidebar__nav" aria-label="Secciones de Vector">
            <ul>
              {SECTIONS.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="nav-link"
                    aria-current={active === item.id ? 'page' : undefined}
                    onClick={() => onNavigate(item.id)}
                  >
                    <span className="nav-link__icon">
                      <Icon name={item.icon} />
                    </span>
                    {item.label}
                  </button>
                </li>
              ))}
              <li>
                <button type="button" className="nav-link" onClick={onOpenProfile}>
                  <span className="nav-link__icon">
                    <Icon name="user" />
                  </span>
                  Perfil
                </button>
              </li>
            </ul>
          </nav>

          <p className="sidebar__footer">
            Vector organiza y simula. No es asesoramiento financiero.
          </p>
        </div>
      </aside>

      <div className="app-shell__body">
        <header className="topbar">
          <div className="topbar__brand">
            <BrandMark />
          </div>

          <div className="topbar__greeting">
            <p className="topbar__hello">{firstName ? 'Hola' : 'Tu plata, con contexto'}</p>
            {firstName && <p className="topbar__name">{firstName}</p>}
          </div>

          <div className="topbar__side">
            <ApiStatus />
            {/* Único control de cierre de sesión: está en la barra superior, visible en
                cualquier tamaño de pantalla. Solo aparece si hay una sesión que cerrar. */}
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
            <button
              type="button"
              className="avatar"
              onClick={onOpenProfile}
              aria-label="Abrir tu perfil"
            >
              <span aria-hidden="true">{initialOf(userName)}</span>
            </button>
          </div>
        </header>

        <main className="content">{children}</main>

        {footer && <footer className="app-footer">{footer}</footer>}
      </div>

      {!copilotOpen && (
        <button type="button" className="fab" onClick={onOpenCopilot} aria-label="Abrir el copiloto">
          <span className="fab__icon">
            <Icon name="sparkle" />
          </span>
          Copiloto
        </button>
      )}

      <nav className="bottom-nav" aria-label="Navegación rápida">
        <ul className="bottom-nav__list">
          {MOBILE_SECTIONS.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="bottom-nav__link"
                aria-current={active === item.id ? 'page' : undefined}
                onClick={() => onNavigate(item.id)}
              >
                <span className="bottom-nav__icon">
                  <Icon name={item.icon} />
                </span>
                {item.label}
              </button>
            </li>
          ))}
          <li>
            <button type="button" className="bottom-nav__link" onClick={onOpenProfile}>
              <span className="bottom-nav__icon">
                <Icon name="user" />
              </span>
              Perfil
            </button>
          </li>
        </ul>
      </nav>
    </div>
  )
}
