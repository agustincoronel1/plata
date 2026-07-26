import { formatDate, formatMoney } from '../services/format'

/**
 * Desglose de cómo se llega al disponible real, y la proyección de fin de mes.
 *
 * Todos los montos vienen calculados del backend; acá solo se muestran. Los restados se
 * marcan con un signo "−" (además del texto) para que no dependa solo del color.
 */
export default function FinancialBreakdown({ summary }) {
  if (!summary) {
    return null
  }

  const rows = [
    { label: 'Saldo actual', value: summary.current_balance, kind: 'base' },
    {
      label: 'Compromisos hasta el próximo ingreso',
      value: summary.pending_commitments_amount,
      kind: 'subtract',
    },
    { label: 'Dinero protegido', value: summary.protected_amount, kind: 'subtract' },
    { label: 'Margen de seguridad', value: summary.safety_buffer, kind: 'subtract' },
  ]

  const forecast = summary.forecast

  return (
    <section className="breakdown" aria-labelledby="desglose">
      <h3 className="breakdown__title" id="desglose">
        Cómo se calcula tu disponible
      </h3>

      <dl className="breakdown__list">
        {rows.map((row) => (
          <div className="breakdown__row" key={row.label}>
            <dt>{row.label}</dt>
            <dd className={`breakdown__amount breakdown__amount--${row.kind}`}>
              {row.kind === 'subtract' && (
                <span className="breakdown__sign" aria-hidden="true">
                  −{' '}
                </span>
              )}
              {row.kind === 'subtract' && <span className="visually-hidden">menos </span>}
              {formatMoney(row.value)}
            </dd>
          </div>
        ))}

        <div className="breakdown__row breakdown__row--total">
          <dt>Disponible real</dt>
          <dd className="breakdown__amount">{formatMoney(summary.available_real)}</dd>
        </div>
      </dl>

      {forecast && (
        <div className="breakdown__forecast">
          <h3 className="breakdown__title">Proyección a fin de mes</h3>
          <dl className="breakdown__list">
            <div className="breakdown__row">
              <dt>Saldo proyectado ({formatDate(forecast.month_end)})</dt>
              <dd className="breakdown__amount">
                {formatMoney(forecast.projected_month_end_balance)}
              </dd>
            </div>
            <div className="breakdown__row">
              <dt>Margen proyectado</dt>
              <dd className="breakdown__amount">
                {formatMoney(forecast.projected_month_end_margin)}
              </dd>
            </div>
          </dl>
          <p className="breakdown__note">{forecast.note}</p>
        </div>
      )}
    </section>
  )
}
