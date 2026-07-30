import Icon from './Icon'
import { formatMoney } from '../services/format'

/**
 * Bloque principal del dashboard: el dinero disponible para usar y, debajo, el margen
 * diario. Todos los números llegan calculados del backend; acá no se hace ninguna cuenta.
 *
 * Reglas de presentación honesta (se mantienen tal cual):
 * - Déficit (available_real < 0): muestra $0 y cuánto falta, sin culpabilizar.
 * - Sin monto diario (falta o venció la fecha de ingreso): muestra "$ —" y el aviso.
 * - Sin resumen todavía: "$ —" y una explicación de qué falta.
 */

const TONES = {
  healthy: { modifier: 'positive', label: 'Al día' },
  deficit: { modifier: 'attention', label: 'Atención' },
  incomplete: { modifier: 'neutral', label: 'Faltan datos' },
}

export default function BalanceHero({ summary, onOpenDetail }) {
  const inDeficit = summary && Number(summary.available_real) < 0
  const daily = summary?.daily_safe_to_spend
  const hasDaily = daily !== null && daily !== undefined

  const spendable = inDeficit ? '0' : summary?.spendable_total
  const tone = summary ? (TONES[summary.status] ?? TONES.incomplete) : null

  let note = 'Plata calculará tu margen real cuando completes tu situación.'
  if (inDeficit) {
    note = `Tus compromisos y reservas superan tu saldo por ${formatMoney(summary.deficit_amount)}.`
  } else if (summary) {
    note = 'Ya descontamos tus compromisos, el dinero protegido y tu margen de seguridad.'
  }

  let dailyNote = null
  if (hasDaily) {
    const days = summary.days_until_income
    dailyNote =
      days && days > 1
        ? `Este es tu margen por día hasta tu próximo ingreso (en ${days} días).`
        : 'Este es tu margen disponible hasta tu próximo ingreso.'
  } else if (summary?.warnings?.length) {
    // Sin monto diario confiable: se muestra el primer aviso determinístico del backend.
    dailyNote = summary.warnings[0]
  }

  return (
    <section className="hero" id="inicio" aria-labelledby="disponible-label">
      <div className="hero__top">
        <h2 className="hero__label" id="disponible-label">
          Disponible para usar
        </h2>
        {tone && (
          <span className={`hero__pill hero__pill--${tone.modifier}`}>
            <span className="hero__pill-dot" aria-hidden="true" />
            {tone.label}
          </span>
        )}
      </div>

      <p className="hero__value">{formatMoney(spendable)}</p>
      <p className="hero__note">{note}</p>

      <div className="hero__daily">
        <span className="hero__daily-label">Podés gastar hoy</span>
        <span className="hero__daily-value">{hasDaily ? formatMoney(daily) : '$ —'}</span>
        {dailyNote && <span className="hero__daily-note">{dailyNote}</span>}
      </div>

      {onOpenDetail && (
        <button type="button" className="hero__detail" onClick={onOpenDetail}>
          Ver el detalle de tu situación
          <Icon name="chevronRight" />
        </button>
      )}
    </section>
  )
}
