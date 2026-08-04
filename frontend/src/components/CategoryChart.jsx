import { categoryLabel } from '../services/categories'
import { formatMoney } from '../services/format'
import EmptyState from './EmptyState'

/**
 * "En qué se fue tu plata": gasto del mes por categoría, en una torta (dona) de SVG puro.
 *
 * No hay librería de gráficos ni cálculo propio: el backend ya devuelve monto y porcentaje
 * por categoría, con el top 5 y el resto agrupado en "otros", ordenado de mayor a menor.
 *
 * Geometría: un círculo de radio 15.9155 tiene circunferencia 100, así que el porcentaje
 * de cada categoría se puede usar tal cual como `stroke-dasharray` sin trigonometría. El
 * `dashoffset` acumulado corre cada porción y el arranque en 12 en punto lo da el giro de
 * -90°. Entre porciones queda un hueco del color de la tarjeta para que se separen sin
 * depender del contraste entre colores vecinos.
 *
 * Colores: paleta categórica validada para fondo oscuro (banda de luminosidad, piso de
 * croma, separación para daltonismo y contraste contra la superficie). Se asignan por
 * posición en la lista ya ordenada; la identidad de cada categoría no la lleva el color
 * sino la leyenda, que siempre muestra el nombre al lado de su muestra.
 */

const SLICE_COLORS = ['#6f9e26', '#3987e5', '#d95926', '#9085e9', '#c98500', '#199e70']

const RADIUS = 15.9155
const CIRCUMFERENCE = 100
// Hueco entre porciones, en las mismas unidades que el porcentaje.
const GAP = 0.8

export default function CategoryChart({ items }) {
  if (!items?.length) {
    return (
      <EmptyState
        icon="sparkle"
        title="Todavía no hay gastos este mes"
        description="Cuando registres un gasto, vas a ver acá en qué se fue tu plata."
      />
    )
  }

  const total = items.reduce((sum, item) => sum + Number(item.amount), 0)

  let offset = 0
  const slices = items.map((item, index) => {
    const percentage = Number(item.percentage)
    const slice = {
      key: item.category,
      label: categoryLabel(item.category),
      amount: item.amount,
      percentage: item.percentage,
      color: SLICE_COLORS[index % SLICE_COLORS.length],
      // Una porción muy chica igual tiene que verse: nunca menos que el hueco.
      dash: Math.max(percentage - GAP, 0.6),
      offset,
    }
    offset += percentage
    return slice
  })

  return (
    <div className="donut">
      <div className="donut__chart">
        <svg viewBox="0 0 42 42" role="img" aria-label="En qué se fue tu plata este mes">
          <title>En qué se fue tu plata este mes</title>
          {slices.map((slice) => (
            <circle
              key={slice.key}
              className="donut__slice"
              cx="21"
              cy="21"
              r={RADIUS}
              stroke={slice.color}
              strokeDasharray={`${slice.dash} ${CIRCUMFERENCE - slice.dash}`}
              strokeDashoffset={CIRCUMFERENCE - slice.offset}
            />
          ))}
        </svg>
        <div className="donut__center">
          <span className="donut__center-label">Gastado</span>
          <span className="donut__center-value">{formatMoney(total)}</span>
        </div>
      </div>

      <ul className="donut__legend">
        {slices.map((slice) => (
          <li className="donut__legend-item" key={slice.key}>
            <span
              className="donut__swatch"
              style={{ backgroundColor: slice.color }}
              aria-hidden="true"
            />
            <span className="donut__legend-name">{slice.label}</span>
            <span className="donut__legend-amount">{formatMoney(slice.amount)}</span>
            <span className="donut__legend-share">{slice.percentage}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
