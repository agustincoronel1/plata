/**
 * Tarjeta de una métrica. Todavía no hay cálculo: el valor llega como texto
 * desde arriba y hoy es siempre un guion.
 */
export default function MetricCard({ label, value, hint }) {
  return (
    <li className="metric">
      <h3 className="metric__label">{label}</h3>
      <p className="metric__value">{value}</p>
      <p className="metric__hint">{hint}</p>
    </li>
  )
}
