import BrandMark from './BrandMark'
import Icon from './Icon'

const POINTS = [
  {
    icon: 'wallet',
    text: 'Descuenta tus compromisos, tu dinero protegido y tu margen de seguridad.',
  },
  { icon: 'clock', text: 'Te muestra cuánto te queda por día hasta que vuelvas a cobrar.' },
  { icon: 'calculator', text: 'Podés simular una compra en cuotas antes de decidirte.' },
]

/**
 * Primera pantalla cuando todavía no hay un perfil cargado (la API responde 404).
 *
 * No muestra el dashboard con ceros ni abre ningún modal solo: explica para qué sirve
 * Plata y deja una única acción principal, que reutiliza el formulario de perfil.
 */
export default function WelcomeScreen({ onStart }) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <BrandMark size="lg" />

      <h2 className="welcome__title" id="welcome-title">
        Tu plata, con contexto.
      </h2>
      <p className="welcome__lead">
        Plata te muestra cuánto podés usar realmente, sin descuidar tus gastos y compromisos.
      </p>

      <ul className="welcome__points">
        {POINTS.map((point) => (
          <li className="welcome__point" key={point.text}>
            <span className="welcome__point-icon">
              <Icon name={point.icon} />
            </span>
            {point.text}
          </li>
        ))}
      </ul>

      <button type="button" className="btn btn--primary btn--block" onClick={onStart}>
        Configurar mi cuenta
      </button>

      <p className="welcome__note">
        Son pocos datos: tu saldo, tu próximo ingreso y lo que quieras dejar aparte.
      </p>
    </section>
  )
}
