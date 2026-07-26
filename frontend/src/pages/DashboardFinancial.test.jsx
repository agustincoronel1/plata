import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage'
import * as api from '../services/api'
import { PROFILE, SUMMARY, makeSimulation } from '../test/fixtures'

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

const { ApiError } = api

const ready = () => screen.findByRole('heading', { name: /Tu situación/i })

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

describe('Dashboard financiero (Día 3)', () => {
  it('consulta el resumen financiero al cargar', async () => {
    render(<DashboardPage />)
    await ready()
    expect(api.getDashboardSummary).toHaveBeenCalled()
  })

  it('"Podés gastar hoy" muestra el monto diario real del backend', async () => {
    render(<DashboardPage />)
    await ready()

    const headline = screen.getByText('Podés gastar hoy').closest('section')
    // daily_safe_to_spend = 26250 -> "$ 26.250"
    expect(headline).toHaveTextContent(/26\.250/)
  })

  it('con disponible negativo muestra $0 y el déficit, no un negativo', async () => {
    api.getDashboardSummary.mockResolvedValue({
      ...SUMMARY,
      available_real: '-35000.00',
      spendable_total: '0.00',
      deficit_amount: '35000.00',
      daily_safe_to_spend: '0.00',
      status: 'deficit',
    })
    render(<DashboardPage />)
    await ready()

    const headline = screen.getByText('Podés gastar hoy').closest('section')
    expect(headline).toHaveTextContent('$ 0')
    expect(headline).toHaveTextContent(/superan tu saldo por/i)
    expect(headline).toHaveTextContent(/35\.000/)
  })

  it('con fecha de ingreso faltante muestra $ — y el aviso', async () => {
    api.getDashboardSummary.mockResolvedValue({
      ...SUMMARY,
      days_until_income: null,
      daily_safe_to_spend: null,
      status: 'incomplete',
      warnings: ['No configuraste la fecha de tu próximo ingreso.'],
    })
    render(<DashboardPage />)
    await ready()

    const headline = screen.getByText('Podés gastar hoy').closest('section')
    expect(headline).toHaveTextContent('$ —')
    expect(headline).toHaveTextContent(/No configuraste la fecha/i)
  })

  it('el desglose muestra compromisos, reservas y disponible real', async () => {
    render(<DashboardPage />)
    await ready()

    const breakdown = screen.getByText(/Cómo se calcula tu disponible/i).closest('section')
    expect(breakdown).toHaveTextContent(/Compromisos hasta el próximo ingreso/i)
    expect(breakdown).toHaveTextContent(/Disponible real/i)
    // pending 250000, disponible 210000
    expect(breakdown).toHaveTextContent(/250\.000/)
    expect(breakdown).toHaveTextContent(/210\.000/)
  })

  it('muestra la proyección de fin de mes con la aclaración', async () => {
    render(<DashboardPage />)
    await ready()

    expect(screen.getByText(/Proyección a fin de mes/i)).toBeInTheDocument()
    expect(screen.getByText(/No estima gastos variables/i)).toBeInTheDocument()
  })

  it('abre el formulario de simulación', async () => {
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Simular compra/i }))

    expect(await screen.findByRole('heading', { name: /Simular compra/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Total final a pagar/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Cantidad de cuotas/i)).toBeInTheDocument()
  })

  it('un error 422 en la simulación muestra un mensaje comprensible', async () => {
    api.createPurchaseSimulation.mockRejectedValue(
      new ApiError('Revisá los datos del formulario.', {
        status: 422,
        fieldErrors: { installments: 'Debe estar entre 1 y 24.' },
      }),
    )
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Simular compra/i }))
    await userEvent.type(screen.getByLabelText(/Nombre de la compra/i), 'Notebook')
    await userEvent.type(screen.getByLabelText(/Total final a pagar/i), '900000')
    await userEvent.click(screen.getByRole('button', { name: /^Simular$/i }))

    expect(await screen.findByText(/Revisá los datos del formulario/i)).toBeInTheDocument()
    expect(screen.queryByText(/traceback|sqlalchemy|psycopg/i)).not.toBeInTheDocument()
  })

  it('crea una simulación y muestra el resultado con el detalle mensual', async () => {
    api.createPurchaseSimulation.mockResolvedValue(makeSimulation())
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Simular compra/i }))
    await userEvent.type(screen.getByLabelText(/Nombre de la compra/i), 'Notebook prueba')
    await userEvent.type(screen.getByLabelText(/Total final a pagar/i), '900000')
    await userEvent.clear(screen.getByLabelText(/Cantidad de cuotas/i))
    await userEvent.type(screen.getByLabelText(/Cantidad de cuotas/i), '9')
    await userEvent.click(screen.getByRole('button', { name: /^Simular$/i }))

    // Resultado visible, con conclusión neutral y comparación con el mes siguiente.
    expect(await screen.findByRole('heading', { name: /Resultado de la simulación/i })).toBeInTheDocument()
    // Conclusión neutral (aparece en el banner y en las tarjetas mensuales sin riesgo).
    expect(screen.getAllByText(/Dentro del margen/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Si empezás el mes siguiente/i)).toBeInTheDocument()
    // Detalle mes a mes renderizado (ago 2026 / sep 2026).
    expect(screen.getByText(/Mes a mes/i)).toBeInTheDocument()
    expect(screen.getByText(/ago 2026/i)).toBeInTheDocument()
    expect(screen.getByText(/Simulación lista/i)).toBeInTheDocument()
  })

  it('muestra el historial de simulaciones recientes', async () => {
    api.getSimulations.mockResolvedValue([makeSimulation({ purchase_name: 'Heladera' })])
    render(<DashboardPage />)
    await ready()

    const history = screen.getByRole('heading', { name: /Simulaciones recientes/i }).closest('section')
    expect(history).toHaveTextContent('Heladera')
  })

  it('el frontend no recalcula el disponible: usa el valor del backend', async () => {
    // Si el backend manda un disponible distinto de la resta ingenua, se respeta ese.
    api.getDashboardSummary.mockResolvedValue({ ...SUMMARY, available_real: '999999.00' })
    render(<DashboardPage />)
    await ready()

    const breakdown = screen.getByText(/Cómo se calcula tu disponible/i).closest('section')
    expect(breakdown).toHaveTextContent(/999\.999/)
  })
})
