import { formatDate, formatMoney } from '../services/format'

const CONCLUSION_LABEL = {
  fits_within_reserves: 'Dentro del margen',
  breaks_reserves: 'Supera las reservas',
  insufficient_data: 'Faltan datos',
}

/**
 * Historial ligero de simulaciones recientes. Solo lectura: no se reabren ni se eliminan
 * todavía.
 */
export default function SimulationHistory({ simulations }) {
  if (!simulations || simulations.length === 0) {
    return <p className="empty">Todavía no simulaste ninguna compra.</p>
  }

  return (
    <ul className="sim-history">
      {simulations.map((sim) => {
        const conclusion = sim.result?.conclusion
        return (
          <li className="sim-history__item" key={sim.id}>
            <div className="sim-history__body">
              <p className="sim-history__name">{sim.purchase_name}</p>
              <p className="sim-history__meta">
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
