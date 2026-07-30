/**
 * Tarjeta de una métrica. El valor llega ya formateado desde arriba: acá no se calcula
 * ni se convierte nada.
 */
export default function MetricCard({ label, value, hint }) {
  return (
    <li className="metric">
      <h3 className="metric__label">{label}</h3>
      <p className="metric__value">{value}</p>
      {hint && <p className="metric__hint">{hint}</p>}
    </li>
  )
}
