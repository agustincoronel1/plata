import { formatDate, formatMonthKey, formatMoney } from '../services/format'

// Conclusiones neutrales: nunca "comprá" / "no compres". Texto + estilo, no solo color.
const CONCLUSION = {
  fits_within_reserves: { label: 'Dentro del margen', tone: 'ok' },
  breaks_reserves: { label: 'Supera las reservas', tone: 'warn' },
  insufficient_data: { label: 'Faltan datos para proyectar', tone: 'muted' },
}

function marginState(breaksReserve) {
  return breaksReserve
    ? { label: 'Supera las reservas', tone: 'warn' }
    : { label: 'Dentro del margen', tone: 'ok' }
}

/**
 * Muestra el resultado de una simulación: resumen, conclusión neutral, comparación con
 * empezar el mes siguiente y el detalle mes a mes en tarjetas (sin overflow horizontal).
 *
 * `simulation` trae los metadatos persistidos y `result` con el cálculo del motor.
 */
export default function SimulationResult({ simulation }) {
  const result = simulation.result
  const conclusion = CONCLUSION[result.conclusion] ?? CONCLUSION.insufficient_data
  const alt = result.start_next_month

  return (
    <div className="sim-result">
      <p className={`sim-conclusion sim-conclusion--${conclusion.tone}`}>
        <span className="sim-conclusion__label">{conclusion.label}</span>
        <span className="sim-conclusion__name"> · {simulation.purchase_name}</span>
      </p>

      <h3 className="sim-result__subtitle">Resumen</h3>
      <dl className="sim-summary">
        <div className="sim-summary__row">
          <dt>Total</dt>
          <dd>{formatMoney(simulation.total_amount)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Cuotas</dt>
          <dd>{result.installment_count}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Cuota aproximada</dt>
          <dd>{formatMoney(result.regular_installment_amount)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Primera cuota</dt>
          <dd>{formatDate(result.first_installment_date)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Última cuota</dt>
          <dd>{formatDate(result.last_installment_date)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Meses que superan reservas</dt>
          <dd>{result.risk_months_count}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Margen mínimo con la compra</dt>
          <dd>{formatMoney(result.minimum_margin_with_purchase)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Saldo final sin la compra</dt>
          <dd>{formatMoney(result.final_balance_without_purchase)}</dd>
        </div>
        <div className="sim-summary__row">
          <dt>Saldo final con la compra</dt>
          <dd>{formatMoney(result.final_balance_with_purchase)}</dd>
        </div>
      </dl>

      {alt && (
        <div className="sim-alt">
          <h3 className="sim-result__subtitle">Si empezás el mes siguiente</h3>
          <p className="sim-alt__line">
            Primera cuota el {formatDate(alt.first_installment_date)}:{' '}
            {alt.risk_months_count} mes(es) que superan reservas, margen mínimo{' '}
            {formatMoney(alt.minimum_margin)}.
          </p>
          <p className={`sim-alt__verdict sim-alt__verdict--${alt.improves_margin ? 'ok' : 'muted'}`}>
            {alt.improves_margin
              ? 'Empezar el mes siguiente mejora tu peor mes.'
              : 'Empezar el mes siguiente no mejora tu peor mes.'}
          </p>
        </div>
      )}

      <h3 className="sim-result__subtitle">Mes a mes</h3>
      <ul className="sim-months">
        {result.months.map((month) => {
          const state = marginState(month.breaks_reserve)
          return (
            <li className="sim-month" key={month.month}>
              <div className="sim-month__head">
                <span className="sim-month__name">{formatMonthKey(month.month)}</span>
                <span className={`tag tag--${state.tone === 'ok' ? 'pending' : 'expense'}`}>
                  {state.label}
                </span>
              </div>
              <dl className="sim-month__grid">
                <div>
                  <dt>Ingresos</dt>
                  <dd>{formatMoney(month.income_amount)}</dd>
                </div>
                <div>
                  <dt>Compromisos</dt>
                  <dd>{formatMoney(month.commitment_amount)}</dd>
                </div>
                <div>
                  <dt>Cuota</dt>
                  <dd>{formatMoney(month.purchase_installment_amount)}</dd>
                </div>
                <div>
                  <dt>Saldo sin compra</dt>
                  <dd>{formatMoney(month.closing_balance_without_purchase)}</dd>
                </div>
                <div>
                  <dt>Saldo con compra</dt>
                  <dd>{formatMoney(month.closing_balance_with_purchase)}</dd>
                </div>
              </dl>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
