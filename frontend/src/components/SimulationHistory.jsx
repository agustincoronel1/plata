import EmptyState from './EmptyState'
import Icon from './Icon'
import { formatDate, formatMoney } from '../services/format'

const CONCLUSION_LABEL = {
  fits_within_reserves: 'Dentro del margen',
  breaks_reserves: 'Supera las reservas',
  insufficient_data: 'Faltan datos',
}

/**
 * Historial ligero de simulaciones recientes. Solo lectura: no se reabren ni se eliminan
 * todavía.
 *
 * Simular nunca toca el saldo ni registra un gasto; la sección lo aclara arriba, para que
 * probar una compra no dé miedo.
 */
export default function SimulationHistory({ simulations, onSimulate }) {
  if (!simulations || simulations.length === 0) {
    return (
      <EmptyState
        icon="calculator"
        title="Todavía no simulaste nada"
        description="Probá una compra en cuotas y mirá cómo te quedaría cada mes antes de decidirte."
      >
        <button type="button" className="btn btn--primary" onClick={onSimulate}>
          Simular una compra
        </button>
      </EmptyState>
    )
  }

  return (
    <ul className="row-list">
      {simulations.map((sim) => {
        const conclusion = sim.result?.conclusion
        return (
          <li className="row" key={sim.id}>
            <span className="row__icon">
              <Icon name="calculator" />
            </span>
            <div className="row__body">
              <p className="row__title">{sim.purchase_name}</p>
              <p className="row__meta">
                {formatMoney(sim.total_amount)} · {sim.installments} cuotas ·{' '}
                {formatDate(sim.first_installment_date)}
              </p>
            </div>
            {conclusion && (
              <span
                className={`tag tag--${conclusion === 'breaks_reserves' ? 'expense' : 'pending'}`}
              >
                {CONCLUSION_LABEL[conclusion] ?? 'Simulación'}
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
