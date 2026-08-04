/**
 * Categorías de gasto: vocabulario fijo, etiquetas y sugerencia automática.
 *
 * Es el espejo en el navegador de `backend/app/services/categorizer.py`. La autoridad la
 * tiene el backend (revalida y resuelve toda categoría que recibe); acá las reglas solo
 * sirven para PROPONER una categoría mientras la persona escribe, sin pedirle nada al
 * servidor ni al modelo. Si las dos listas se desalinean, lo peor que pasa es que la
 * sugerencia no sea la ideal: el backend nunca guarda una categoría fuera de la lista.
 */

export const OTHER_CATEGORY = 'otros'

export const EXPENSE_CATEGORIES = [
  'comida',
  'transporte',
  'vivienda',
  'servicios',
  'salud',
  'suscripciones',
  'compras',
  'ocio',
  'educación',
  OTHER_CATEGORY,
]

export const CATEGORY_LABELS = {
  comida: 'Comida',
  transporte: 'Transporte',
  vivienda: 'Vivienda',
  servicios: 'Servicios',
  salud: 'Salud',
  suscripciones: 'Suscripciones',
  compras: 'Compras',
  ocio: 'Ocio',
  educación: 'Educación',
  otros: 'Otros',
}

// Mismo orden que el backend: gana la primera categoría que coincide.
const KEYWORDS = [
  [
    'transporte',
    ['nafta', 'combustible', 'gasoil', 'ypf', 'shell', 'axion', 'puma', 'estacion de servicio',
      'colectivo', 'subte', 'tren', 'taxi', 'remis', 'uber', 'cabify', 'didi', 'sube', 'peaje',
      'estacionamiento', 'cochera'],
  ],
  [
    'suscripciones',
    ['netflix', 'spotify', 'youtube', 'disney', 'hbo', 'prime video', 'suscripcion', 'abono mensual'],
  ],
  ['vivienda', ['alquiler', 'expensas', 'inmobiliaria', 'hipoteca']],
  [
    'servicios',
    ['luz', 'gas', 'agua', 'internet', 'wifi', 'telefono', 'celular', 'cable', 'edenor', 'edesur',
      'metrogas', 'aysa'],
  ],
  [
    'salud',
    ['farmacia', 'medico', 'medica', 'dentista', 'odontologo', 'clinica', 'hospital', 'obra social',
      'prepaga', 'kinesiologia'],
  ],
  [
    'educación',
    ['curso', 'cursada', 'facultad', 'universidad', 'colegio', 'escuela', 'libro', 'matricula',
      'capacitacion'],
  ],
  [
    'comida',
    ['supermercado', 'super', 'comida', 'almuerzo', 'cena', 'desayuno', 'restaurante', 'resto',
      'delivery', 'pedidosya', 'rappi', 'almacen', 'carniceria', 'verduleria', 'panaderia',
      'fiambreria', 'dietetica', 'kiosco', 'cafe', 'cafeteria', 'coto', 'carrefour', 'jumbo'],
  ],
  [
    'ocio',
    ['cine', 'bar', 'boliche', 'salida', 'juego', 'teatro', 'recital', 'streaming de juegos',
      'gimnasio', 'vacaciones'],
  ],
  [
    'compras',
    ['ropa', 'zapatillas', 'calzado', 'indumentaria', 'mercado libre', 'electronica',
      'electrodomestico', 'regalo', 'mueble', 'shopping'],
  ],
]

/** Minúsculas, sin acentos y con espacios colapsados. */
export function normalize(text) {
  if (!text) return ''
  return text
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Mn}/gu, '')
    .split(/\s+/)
    .join(' ')
}

// Índice por forma normalizada: "Educación", "educación" y "educacion" son la misma.
const BY_NORMALIZED = new Map(EXPENSE_CATEGORIES.map((name) => [normalize(name), name]))

/** Etiqueta legible de una categoría (si no la conocemos, se muestra tal cual). */
export function categoryLabel(category) {
  if (!category) return CATEGORY_LABELS[OTHER_CATEGORY]
  const canonical = BY_NORMALIZED.get(normalize(category))
  return canonical ? CATEGORY_LABELS[canonical] : category
}

/** True si la categoría pertenece al vocabulario fijo de gastos. */
export function isExpenseCategory(category) {
  return BY_NORMALIZED.has(normalize(category))
}

/**
 * Categoría sugerida para un gasto a partir del texto disponible. Sin coincidencia: `otros`.
 * Una palabra clave simple matchea una palabra completa (o su plural), no una subcadena.
 */
export function suggestCategory(...texts) {
  const haystack = texts.map(normalize).filter(Boolean).join(' ')
  if (!haystack) return OTHER_CATEGORY

  const words = new Set(haystack.split(' '))
  for (const [category, keywords] of KEYWORDS) {
    const hit = keywords.some((keyword) =>
      keyword.includes(' ') ? haystack.includes(keyword) : words.has(keyword) || words.has(`${keyword}s`),
    )
    if (hit) return category
  }
  return OTHER_CATEGORY
}
