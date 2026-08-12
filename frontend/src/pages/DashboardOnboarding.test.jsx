import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage'
import * as api from '../services/api'
import { PROFILE, SUMMARY, makeCommitment, makeSimulation, makeTransaction } from '../test/fixtures'

// Misma estrategia que el resto de los tests del dashboard: la capa de API se mockea y
// ApiError se mantiene real para que las ramas de error funcionen igual.
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

const sectionOf = (name) => screen.getByRole('heading', { name }).closest('section')

function noProfile() {
  api.getProfile.mockRejectedValue(
    new ApiError('Perfil financiero no encontrado', { status: 404 }),
  )
}

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

describe('Bienvenida sin perfil', () => {
  it('explica qué hace Vector y no muestra un dashboard vacío', async () => {
    noProfile()
    render(<DashboardPage />)

    expect(await screen.findByRole('heading', { name: /Tu plata, con contexto/i })).toBeInTheDocument()
    expect(screen.getByText(/cuánto podés usar realmente/i)).toBeInTheDocument()

    // Nada del dashboard con datos: ni métricas, ni listas, ni copiloto, ni ceros sueltos.
    expect(screen.queryByRole('heading', { name: /Tu situación/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Movimientos recientes/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Próximos compromisos/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Copiloto financiero/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Podés gastar hoy')).not.toBeInTheDocument()
    // Y tampoco un mensaje de error: no tener perfil todavía no es una falla.
    expect(screen.queryByRole('heading', { name: /Algo salió mal/i })).not.toBeInTheDocument()
  })

  it('no abre ningún modal solo: el formulario aparece recién al tocar el botón', async () => {
    noProfile()
    render(<DashboardPage />)

    const empezar = await screen.findByRole('button', { name: /Configurar mi cuenta/i })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await userEvent.click(empezar)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Configurá tu situación/i })).toBeInTheDocument()
    // Es el formulario de perfil de siempre, con sus campos reales.
    expect(within(dialog).getByLabelText(/Saldo actual/i)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Dinero protegido/i)).toBeInTheDocument()
  })

  it('el botón de bienvenida se puede accionar con el teclado', async () => {
    noProfile()
    render(<DashboardPage />)

    const empezar = await screen.findByRole('button', { name: /Configurar mi cuenta/i })
    empezar.focus()
    expect(empezar).toHaveFocus()
    await userEvent.keyboard('{Enter}')

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('al guardar el perfil se carga el dashboard normal', async () => {
    noProfile()
    api.updateProfile.mockResolvedValue(PROFILE)
    render(<DashboardPage />)

    await userEvent.click(await screen.findByRole('button', { name: /Configurar mi cuenta/i }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/Nombre/i), 'Agustín Demo')
    await userEvent.type(within(dialog).getByLabelText(/Saldo actual/i), '620000')
    await userEvent.click(within(dialog).getByRole('button', { name: /^Guardar$/i }))

    await ready()
    expect(screen.queryByRole('heading', { name: /Tu plata, con contexto/i })).not.toBeInTheDocument()
    expect(screen.getAllByText(/620\.000/).length).toBeGreaterThan(0)
    expect(api.updateProfile).toHaveBeenCalledTimes(1)
  })
})

describe('Estados vacíos con perfil cargado', () => {
  it('movimientos vacíos explican qué hacer y ofrecen las dos formas de cargar', async () => {
    render(<DashboardPage />)
    await ready()

    const seccion = sectionOf(/Movimientos recientes/i)
    expect(within(seccion).getByRole('heading', { name: /Todavía no hay movimientos/i })).toBeInTheDocument()
    expect(within(seccion).getByText(/Registrá tu primer ingreso o gasto/i)).toBeInTheDocument()
    expect(within(seccion).getByRole('button', { name: /Registrar manualmente/i })).toBeInTheDocument()
    expect(within(seccion).getByRole('button', { name: /Escribilo con IA/i })).toBeInTheDocument()
  })

  it('"Registrar manualmente" abre el formulario de movimiento existente', async () => {
    render(<DashboardPage />)
    await ready()

    const seccion = sectionOf(/Movimientos recientes/i)
    await userEvent.click(within(seccion).getByRole('button', { name: /Registrar manualmente/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Registrar movimiento/i })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Monto/i)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Categoría/i)).toBeInTheDocument()
  })

  it('"Escribilo con IA" desde el estado vacío abre el diálogo de IA', async () => {
    render(<DashboardPage />)
    await ready()

    const seccion = sectionOf(/Movimientos recientes/i)
    await userEvent.click(within(seccion).getByRole('button', { name: /Escribilo con IA/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Escribilo con IA/i })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Contá qué pasó/i)).toBeInTheDocument()
    // Se mantiene la promesa de confirmación: nada se registra sin aprobación.
    expect(within(dialog).getByText(/No registra nada hasta que lo confirmes/i)).toBeInTheDocument()
  })

  it('compromisos vacíos explican para qué sirven sin ocultar la sección', async () => {
    render(<DashboardPage />)
    await ready()

    const seccion = sectionOf(/Próximos compromisos/i)
    expect(within(seccion).getByRole('heading', { name: /Todavía no hay compromisos/i })).toBeInTheDocument()
    expect(within(seccion).getByText(/los descuenta de tu disponible/i)).toBeInTheDocument()
    await userEvent.click(
      within(seccion).getByRole('button', { name: /Agregar tu primer compromiso/i }),
    )

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/Fecha de vencimiento/i)).toBeInTheDocument()
  })

  it('simulaciones vacías aclaran que simular no toca el saldo y dejan la acción visible', async () => {
    render(<DashboardPage />)
    await ready()

    const seccion = sectionOf(/Simulaciones recientes/i)
    expect(within(seccion).getByRole('heading', { name: /Todavía no simulaste nada/i })).toBeInTheDocument()
    expect(
      within(seccion).getByText(/Simular no registra un gasto ni modifica tu saldo/i),
    ).toBeInTheDocument()
    await userEvent.click(within(seccion).getByRole('button', { name: /Simular una compra/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Simular compra/i })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Cantidad de cuotas/i)).toBeInTheDocument()
  })

  it('el copiloto arranca con ejemplos y no envía ninguna consulta sola', async () => {
    render(<DashboardPage />)
    await ready()

    const copiloto = sectionOf(/Copiloto financiero/i)
    const ejemplos = within(copiloto).getByRole('group', { name: /Podés escribirle o probar con un ejemplo/i })
    expect(within(ejemplos).getByRole('button', { name: '¿Cuánto puedo gastar hoy?' })).toBeInTheDocument()
    expect(within(ejemplos).getByRole('button', { name: 'Gasté 25 mil en combustible.' })).toBeInTheDocument()
    expect(
      within(ejemplos).getByRole('button', { name: '¿Me conviene comprar una notebook en cuotas?' }),
    ).toBeInTheDocument()
    // Sin conversación iniciada no hay respuesta ni "Pensando…" en pantalla.
    expect(within(copiloto).queryByText(/Pensando…/i)).not.toBeInTheDocument()
  })
})

describe('Dashboard con datos', () => {
  beforeEach(() => {
    api.getTransactions.mockResolvedValue([makeTransaction()])
    api.getCommitments.mockResolvedValue([makeCommitment()])
    api.getSimulations.mockResolvedValue([makeSimulation()])
  })

  it('no muestra bienvenida ni estados vacíos cuando hay información cargada', async () => {
    render(<DashboardPage />)
    await ready()

    expect(screen.queryByRole('heading', { name: /Tu plata, con contexto/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Todavía no hay movimientos/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Todavía no hay compromisos/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Todavía no simulaste nada/i })).not.toBeInTheDocument()

    expect(within(sectionOf(/Próximos compromisos/i)).getByText('Alquiler')).toBeInTheDocument()
    expect(within(sectionOf(/Simulaciones recientes/i)).getByText(/Notebook prueba/i)).toBeInTheDocument()
  })

  it('sigue pidiendo confirmación para eliminar un movimiento', async () => {
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Eliminar movimiento de comida/i }))

    expect(await screen.findByRole('heading', { name: /Eliminar movimiento/i })).toBeInTheDocument()
    expect(api.deleteTransaction).not.toHaveBeenCalled()
  })
})
