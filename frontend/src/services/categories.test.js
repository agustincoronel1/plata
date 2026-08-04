import { describe, expect, it } from 'vitest'

import {
  EXPENSE_CATEGORIES,
  OTHER_CATEGORY,
  categoryLabel,
  isExpenseCategory,
  suggestCategory,
} from './categories'

describe('suggestCategory', () => {
  it.each([
    ['Nafta', 'transporte'],
    ['Carga de combustible', 'transporte'],
    ['Compra en el supermercado', 'comida'],
    ['Pedido por PedidosYa', 'comida'],
    ['Alquiler', 'vivienda'],
    ['Pagué la luz', 'servicios'],
    ['Farmacia', 'salud'],
    ['Netflix', 'suscripciones'],
    ['Zapatillas', 'compras'],
    ['Cine', 'ocio'],
    ['Curso de inglés', 'educación'],
  ])('sugiere %s -> %s', (texto, esperado) => {
    expect(suggestCategory(texto)).toBe(esperado)
  })

  it('normaliza mayúsculas y acentos', () => {
    expect(suggestCategory('ESTACIÓN DE SERVICIO')).toBe('transporte')
  })

  it('sin coincidencia cae en otros', () => {
    expect(suggestCategory('')).toBe(OTHER_CATEGORY)
    expect(suggestCategory('algo raro')).toBe(OTHER_CATEGORY)
    // Palabra completa, no subcadena: "barrio" no es "bar".
    expect(suggestCategory('compré en el barrio')).toBe(OTHER_CATEGORY)
  })
})

describe('categorías', () => {
  it('todas las categorías tienen etiqueta', () => {
    for (const name of EXPENSE_CATEGORIES) {
      expect(categoryLabel(name)).toBeTruthy()
      expect(isExpenseCategory(name)).toBe(true)
    }
  })

  it('una categoría vieja de texto libre se muestra tal cual', () => {
    expect(categoryLabel('supermercado')).toBe('supermercado')
    expect(isExpenseCategory('supermercado')).toBe(false)
  })

  it('acepta la categoría sin acentos', () => {
    expect(isExpenseCategory('educacion')).toBe(true)
    expect(categoryLabel('educacion')).toBe('Educación')
  })
})
