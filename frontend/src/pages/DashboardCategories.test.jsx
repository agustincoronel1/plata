import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage'
import * as api from '../services/api'
import { PROFILE, SUMMARY } from '../test/fixtures'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    getProfile: vi.fn(),
    updateProfile: vi.fn(),
    getTransactions: vi.fn(),
    createTransaction: vi.fn(),
    updateTransaction: vi.fn(),
    deleteTransaction: vi.fn(),
    getCommitments: vi.fn(),
    createCommitment: vi.fn(),
    updateCommitment: vi.fn(),
    deleteCommitment: vi.fn(),
    getDashboardSummary: vi.fn(),
    createPurchaseSimulation: vi.fn(),
    getSimulations: vi.fn(),
    fetchApiHealth: vi.fn(),
  }
})

const ready = () => screen.findByRole('heading', { name: /Tu situación/i })

const section = (name) => screen.getByRole('heading', { name }).closest('section')

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchApiHealth.mockResolvedValue({ ok: true, version: '0.1.0' })
  api.getProfile.mockResolvedValue(PROFILE)
  api.getTransactions.mockResolvedValue([])
  api.getCommitments.mockResolvedValue([])
  api.getDashboardSummary.mockResolvedValue(SUMMARY)
  api.getSimulations.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Gasto por categoría en el dashboard', () => {
  it('muestra "En qué se fue tu plata" con la torta y su leyenda', async () => {
    const { container } = render(<DashboardPage />)
    await ready()

    const categorias = section(/En qué se fue tu plata/i)
    expect(container.querySelectorAll('.donut__slice')).toHaveLength(3)
    expect(within(categorias).getByText('Transporte')).toBeInTheDocument()
    expect(within(categorias).getByText('50.0%')).toBeInTheDocument()
    expect(within(categorias).getByText(/35\.000/)).toBeInTheDocument()
  })

  it('sin gastos muestra el estado vacío en lugar del gráfico', async () => {
    api.getDashboardSummary.mockResolvedValue({
      ...SUMMARY,
      month_expenses_total: '0.00',
      category_summary: [],
    })
    const { container } = render(<DashboardPage />)
    await ready()

    expect(screen.getByText(/Todavía no hay gastos este mes/i)).toBeInTheDocument()
    expect(container.querySelectorAll('.donut__slice')).toHaveLength(0)
  })

  it('la fila de ingresos, gastos y ahorro usa los valores del resumen', async () => {
    render(<DashboardPage />)
    await ready()

    const mes = screen.getByRole('list', { name: /Tu mes/i })
    expect(within(mes).getByText('Ingresos')).toBeInTheDocument()
    expect(within(mes).getByText(/1\.200\.000/)).toBeInTheDocument()
    expect(within(mes).getByText('Gastos')).toBeInTheDocument()
    expect(within(mes).getByText(/70\.000/)).toBeInTheDocument()
    expect(within(mes).getByText('Ahorro')).toBeInTheDocument()
    expect(within(mes).getByText(/1\.130\.000/)).toBeInTheDocument()
  })

  it('"Tu situación" resume en una frase y deja el detalle a un clic', async () => {
    render(<DashboardPage />)
    await ready()

    const situacion = section(/Tu situación/i)
    expect(within(situacion).getByText(/gastaste 30% menos que el mes pasado/i)).toBeInTheDocument()

    // El desglose completo sigue estando, plegado.
    const detalle = within(situacion).getByText(/Ver el detalle del cálculo/i)
    expect(detalle.closest('details')).not.toHaveAttribute('open')
    await userEvent.click(detalle)
    expect(detalle.closest('details')).toHaveAttribute('open')
    expect(within(situacion).getByText(/Disponible real/i)).toBeInTheDocument()
  })

  it('el bloque principal muestra el disponible y el saldo juntos', async () => {
    render(<DashboardPage />)
    await ready()

    const hero = screen.getByText('Podés gastar hoy').closest('section')
    expect(hero).toHaveTextContent(/210\.000/) // disponible
    expect(hero).toHaveTextContent(/Saldo actual/)
    expect(hero).toHaveTextContent(/620\.000/) // saldo
    expect(hero).toHaveTextContent(/sin comprometer tus gastos fijos/i)
  })
})
