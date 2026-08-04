import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import CategoryChart from './CategoryChart'

const ITEMS = [
  { category: 'transporte', amount: '35000.00', percentage: '50.0' },
  { category: 'comida', amount: '21000.00', percentage: '30.0' },
  { category: 'otros', amount: '14000.00', percentage: '20.0' },
]

describe('CategoryChart', () => {
  it('dibuja una porción por categoría y la leyenda con monto y porcentaje', () => {
    const { container } = render(<CategoryChart items={ITEMS} />)

    expect(container.querySelectorAll('.donut__slice')).toHaveLength(3)

    const leyenda = screen.getByRole('list')
    expect(within(leyenda).getByText('Transporte')).toBeInTheDocument()
    expect(within(leyenda).getByText('Comida')).toBeInTheDocument()
    expect(within(leyenda).getByText('Otros')).toBeInTheDocument()
    expect(within(leyenda).getByText(/35\.000/)).toBeInTheDocument()
    expect(within(leyenda).getByText('50.0%')).toBeInTheDocument()
  })

  it('las porciones cubren la torta completa sin superponerse', () => {
    const { container } = render(<CategoryChart items={ITEMS} />)
    const offsets = [...container.querySelectorAll('.donut__slice')].map((slice) =>
      Number(slice.getAttribute('stroke-dashoffset')),
    )
    // Circunferencia 100: cada porción arranca donde termina la anterior (100, 50, 20).
    expect(offsets).toEqual([100, 50, 20])
  })

  it('muestra el total gastado en el centro', () => {
    render(<CategoryChart items={ITEMS} />)
    expect(screen.getByText(/70\.000/)).toBeInTheDocument()
  })

  it('sin gastos muestra el estado vacío y ningún gráfico', () => {
    const { container } = render(<CategoryChart items={[]} />)

    expect(screen.getByText(/Todavía no hay gastos este mes/i)).toBeInTheDocument()
    expect(container.querySelector('svg.donut__chart')).toBeNull()
    expect(container.querySelectorAll('.donut__slice')).toHaveLength(0)
  })

  it('sin datos todavía no rompe', () => {
    render(<CategoryChart items={undefined} />)
    expect(screen.getByText(/Todavía no hay gastos este mes/i)).toBeInTheDocument()
  })
})
