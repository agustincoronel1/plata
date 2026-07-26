/**
 * Utilidades de PRESENTACIÓN. Nada acá calcula plata: solo da formato a datos que ya
 * vienen resueltos del backend. El dinero llega como string Decimal y se convierte a
 * número únicamente para mostrarlo; ningún monto se suma ni se resta en el frontend.
 */

const arsFormatter = new Intl.NumberFormat('es-AR', {
  style: 'currency',
  currency: 'ARS',
  // El peso argentino no usa centavos en la práctica; los montos grandes se leen mejor
  // sin decimales. La precisión real vive en el backend (Decimal / Numeric).
  maximumFractionDigits: 0,
})

/** Formatea un monto (string Decimal o número) como moneda argentina. */
export function formatMoney(value) {
  if (value === null || value === undefined || value === '') {
    return '$ —'
  }
  const amount = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(amount)) {
    return '$ —'
  }
  return arsFormatter.format(amount)
}

const dateFormatter = new Intl.DateTimeFormat('es-AR', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/** Parsea "YYYY-MM-DD" como fecha local, sin corrimientos por zona horaria. */
function parseIsoDate(value) {
  if (!value) return null
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return null
  return new Date(year, month - 1, day)
}

/** Formatea "YYYY-MM-DD" como fecha legible (p. ej. "1 ago 2026"). */
export function formatDate(value) {
  const date = parseIsoDate(value)
  return date ? dateFormatter.format(date) : '—'
}

const monthFormatter = new Intl.DateTimeFormat('es-AR', {
  month: 'short',
  year: 'numeric',
})

/** Formatea una clave "YYYY-MM" como "ago 2026". */
export function formatMonthKey(value) {
  if (!value) return '—'
  const [year, month] = value.split('-').map(Number)
  if (!year || !month) return value
  return monthFormatter.format(new Date(year, month - 1, 1))
}

function startOfToday() {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

/**
 * Días entre hoy y una fecha. Es diferencia de calendario, no un motor financiero.
 * Devuelve un entero (negativo si ya pasó) o null si no hay fecha.
 */
export function daysUntil(value) {
  const target = parseIsoDate(value)
  if (!target) return null
  const millisPerDay = 24 * 60 * 60 * 1000
  return Math.round((target - startOfToday()) / millisPerDay)
}

/** Texto para la tarjeta "Días hasta cobrar": honesto con fechas nulas o vencidas. */
export function daysUntilLabel(value) {
  const days = daysUntil(value)
  if (days === null) return '—'
  if (days === 0) return 'Hoy'
  if (days < 0) return 'Fecha vencida'
  return String(days)
}
