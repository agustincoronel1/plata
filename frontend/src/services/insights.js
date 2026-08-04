/**
 * Conclusión breve de "Tu situación", armada SOLO con datos ya calculados por el backend
 * (gastos del mes, gastos del mes anterior y categoría principal). No hay IA, no hay
 * endpoint nuevo y no se infiere nada: si falta el dato, la frase no se dice.
 */

import { categoryLabel } from './categories'

/** Variación porcentual entera entre dos montos. `null` si no hay base para comparar. */
function variation(current, previous) {
  if (!(previous > 0)) return null
  return Math.round(((current - previous) / previous) * 100)
}

export function buildMonthInsight(summary) {
  if (!summary) return null

  const expenses = Number(summary.month_expenses_total ?? 0)
  const previous = Number(summary.previous_month_expenses_total ?? 0)
  const top = summary.category_summary?.[0]

  if (!expenses) {
    return 'Todavía no registraste gastos este mes.'
  }

  const parts = []
  const change = variation(expenses, previous)
  if (change === null) {
    parts.push('Es tu primer mes con gastos registrados')
  } else if (change <= -5) {
    parts.push(`Vas bien: gastaste ${Math.abs(change)}% menos que el mes pasado`)
  } else if (change >= 5) {
    parts.push(`Ojo: gastaste ${change}% más que el mes pasado`)
  } else {
    parts.push('Estás gastando parecido al mes pasado')
  }

  if (top) {
    parts.push(`${categoryLabel(top.category)} fue tu categoría principal (${top.percentage}%)`)
  }

  return `${parts.join(' y ')}.`
}
