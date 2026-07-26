import { formatMoney } from '../services/format'

/**
 * Bloque principal "Podés gastar hoy". Muestra el monto diario seguro que calcula el
 * backend, sin hacer ninguna cuenta en el frontend.
 *
 * Reglas de presentación honesta:
 * - Déficit (available_real < 0): muestra $0 y explica cuánto falta, sin culpabilizar.
 * - Sin monto diario (falta o venció la fecha de ingreso): muestra "$ —" y el aviso.
 * - Caso normal: muestra el monto diario.
 */
export default function SpendableHeadline({ summary }) {
  const inDeficit = summary && Number(summary.available_real) < 0
  const daily = summary?.daily_safe_to_spend
  const hasDaily = daily !== null && daily !== undefined

  let value = '$ —'
  let note = 'Plata calculará tu margen real cuando completes tu situación.'

  if (inDeficit) {
    value = '$ 0'
    note = `Tus compromisos y reservas superan tu saldo por ${formatMoney(summary.deficit_amount)}.`
  } else if (hasDaily) {
    value = formatMoney(daily)
    const days = summary.days_until_income
    note =
      days && days > 1
        ? `Este es tu margen por día hasta tu próximo ingreso (en ${days} días).`
        : 'Este es tu margen disponible hasta tu próximo ingreso.'
  } else if (summary?.warnings?.length) {
    // Sin monto diario confiable: mostramos el primer aviso determinístico del backend.
    note = summary.warnings[0]
  }

  return (
    <section className="headline" aria-labelledby="podes-gastar">
      <h2 className="headline__label" id="podes-gastar">
        Podés gastar hoy
      </h2>
      <p className="headline__value">{value}</p>
      <p className="headline__note">{note}</p>
    </section>
  )
}
