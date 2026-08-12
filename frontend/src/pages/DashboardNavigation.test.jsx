import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage'
import * as api from '../services/api'
import { PROFILE, SUMMARY } from '../test/fixtures'

// Misma estrategia que el resto de los tests del dashboard: se mockea la capa de API.
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
    chatCopilot: vi.fn(),
  }
})

const ready = () => screen.findByRole('heading', { name: /Tu situación/i })

// Las dos navegaciones conviven en el DOM y CSS decide cuál se ve, así que cada consulta
// se hace dentro de su landmark.
const desktopNav = () => screen.getByRole('navigation', { name: /Secciones de Vector/i })
const mobileNav = () => screen.getByRole('navigation', { name: /Navegación rápida/i })

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

describe('Navegación de la aplicación', () => {
  it('ofrece las secciones en desktop y los destinos principales en mobile', async () => {
    render(<DashboardPage />)
    await ready()

    for (const label of ['Inicio', 'Movimientos', 'Compromisos', 'Simulador', 'Copiloto', 'Perfil']) {
      expect(within(desktopNav()).getByRole('button', { name: label })).toBeInTheDocument()
    }

    for (const label of ['Inicio', 'Movimientos', 'Simulador', 'Copiloto', 'Perfil']) {
      expect(within(mobileNav()).getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('cada sección navegable existe con su id en la página', async () => {
    const { container } = render(<DashboardPage />)
    await ready()

    for (const id of ['inicio', 'movimientos', 'compromisos', 'simulaciones', 'copiloto']) {
      expect(container.querySelector(`#${id}`)).not.toBeNull()
    }
  })

  it('marca la sección activa al navegar', async () => {
    render(<DashboardPage />)
    await ready()

    const inicio = within(desktopNav()).getByRole('button', { name: 'Inicio' })
    const movimientos = within(desktopNav()).getByRole('button', { name: 'Movimientos' })
    expect(inicio).toHaveAttribute('aria-current', 'page')

    await userEvent.click(movimientos)

    expect(movimientos).toHaveAttribute('aria-current', 'page')
    expect(inicio).not.toHaveAttribute('aria-current')
    // La navegación mobile refleja el mismo estado.
    expect(within(mobileNav()).getByRole('button', { name: 'Movimientos' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('"Perfil" abre el formulario de situación que ya existía', async () => {
    render(<DashboardPage />)
    await ready()

    await userEvent.click(within(mobileNav()).getByRole('button', { name: 'Perfil' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Editar situación/i })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Saldo actual/i)).toBeInTheDocument()
  })

  it('el avatar del header también lleva al perfil y tiene nombre accesible', async () => {
    render(<DashboardPage />)
    await ready()

    const avatar = screen.getByRole('button', { name: /Abrir tu perfil/i })
    await userEvent.click(avatar)

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })
})

describe('Acciones rápidas', () => {
  it('muestra las cuatro acciones con nombre accesible', async () => {
    render(<DashboardPage />)
    await ready()

    const quick = screen.getByRole('region', { name: /Acciones rápidas/i })
    for (const label of [
      'Registrar gasto',
      'Registrar ingreso',
      'Escribilo con IA',
      'Simular compra',
    ]) {
      expect(within(quick).getByRole('button', { name: label })).toBeEnabled()
    }
  })

  it('"Registrar gasto" abre el formulario con el tipo gasto preseleccionado', async () => {
    render(<DashboardPage />)
    await ready()

    const quick = screen.getByRole('region', { name: /Acciones rápidas/i })
    await userEvent.click(within(quick).getByRole('button', { name: 'Registrar gasto' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Registrar movimiento/i })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Tipo/i)).toHaveValue('expense')
  })

  it('"Registrar ingreso" abre el mismo formulario con el tipo ingreso', async () => {
    render(<DashboardPage />)
    await ready()

    const quick = screen.getByRole('region', { name: /Acciones rápidas/i })
    await userEvent.click(within(quick).getByRole('button', { name: 'Registrar ingreso' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/Tipo/i)).toHaveValue('income')
    // Es el formulario manual de siempre, no un flujo nuevo.
    expect(within(dialog).getByLabelText(/Categoría/i)).toBeInTheDocument()
  })

  it('"Escribilo con IA" abre el borrador asistido y sigue aclarando que no registra nada', async () => {
    render(<DashboardPage />)
    await ready()

    const quick = screen.getByRole('region', { name: /Acciones rápidas/i })
    await userEvent.click(within(quick).getByRole('button', { name: 'Escribilo con IA' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Escribilo con IA/i })).toBeInTheDocument()
    expect(within(dialog).getByText(/No registra nada hasta que lo confirmes/i)).toBeInTheDocument()
  })
})

describe('Copiloto en mobile', () => {
  it('el botón flotante abre el copiloto y deja de ofrecerse mientras está abierto', async () => {
    render(<DashboardPage />)
    await ready()

    const dock = document.getElementById('copiloto')
    expect(dock).toHaveAttribute('data-open', 'false')

    await userEvent.click(screen.getByRole('button', { name: /Abrir el copiloto/i }))

    expect(dock).toHaveAttribute('data-open', 'true')
    expect(screen.queryByRole('button', { name: /Abrir el copiloto/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Cerrar el copiloto/i })).toBeInTheDocument()
  })

  it('la navegación inferior también abre el copiloto y se puede cerrar', async () => {
    render(<DashboardPage />)
    await ready()

    await userEvent.click(within(mobileNav()).getByRole('button', { name: 'Copiloto' }))
    const dock = document.getElementById('copiloto')
    expect(dock).toHaveAttribute('data-open', 'true')

    await userEvent.click(screen.getByRole('button', { name: /Cerrar el copiloto/i }))
    expect(dock).toHaveAttribute('data-open', 'false')
    // La conversación no se desmonta: los ejemplos siguen disponibles.
    expect(screen.getByRole('button', { name: '¿Cuánto puedo gastar hoy?' })).toBeInTheDocument()
  })

  it('cerrar con Escape no envía ninguna consulta al copiloto', async () => {
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Abrir el copiloto/i }))
    await userEvent.keyboard('{Escape}')

    expect(document.getElementById('copiloto')).toHaveAttribute('data-open', 'false')
    expect(api.chatCopilot).not.toHaveBeenCalled()
  })
})

describe('Accesibilidad de la estructura', () => {
  it('los botones de icono de un movimiento tienen nombre accesible', async () => {
    api.getTransactions.mockResolvedValue([
      {
        id: 'tx-9',
        type: 'expense',
        amount: '15000.00',
        category: 'comida',
        description: null,
        occurred_on: '2026-07-20',
        payment_method: null,
      },
    ])
    render(<DashboardPage />)
    await ready()

    expect(screen.getByRole('button', { name: /Editar movimiento de comida/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Eliminar movimiento de comida/i })).toBeInTheDocument()
  })

  it('el saldo disponible se anuncia con su etiqueta y el margen diario del backend', async () => {
    render(<DashboardPage />)
    await ready()

    const hero = screen.getByRole('region', { name: /Disponible para usar/i })
    // spendable_total = 210000 y daily_safe_to_spend = 26250, ambos del backend.
    expect(hero).toHaveTextContent(/210\.000/)
    expect(hero).toHaveTextContent(/26\.250/)
  })
})
