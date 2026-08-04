import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage'
import * as api from '../services/api'
import { PROFILE, SUMMARY, makeCommitment, makeTransaction } from '../test/fixtures'

// Mockeamos la capa de API: los tests no dependen del backend real. ApiError se mantiene
// real (importOriginal) para que las ramas de error del componente funcionen igual.
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

// Señal de "dashboard listo" que no colisiona con montos repetidos en pantalla.
const ready = () => screen.findByRole('heading', { name: /Tu situación/i })

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchApiHealth.mockResolvedValue({ ok: true, version: '0.1.0' })
  api.getTransactions.mockResolvedValue([])
  api.getCommitments.mockResolvedValue([])
  api.getDashboardSummary.mockResolvedValue(SUMMARY)
  api.getSimulations.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('DashboardPage', () => {
  it('muestra el estado de carga inicialmente', () => {
    api.getProfile.mockReturnValue(new Promise(() => {})) // nunca resuelve
    render(<DashboardPage />)
    expect(screen.getByText(/Cargando tu situación/i)).toBeInTheDocument()
  })

  it('reemplaza los placeholders con los datos reales del perfil', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    render(<DashboardPage />)
    await ready()

    // El saldo y el dinero protegido reales aparecen (tarjeta y desglose).
    expect(screen.getAllByText(/620\.000/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/120\.000/).length).toBeGreaterThan(0)
  })

  it('muestra la bienvenida si el perfil no existe (404)', async () => {
    api.getProfile.mockRejectedValue(new ApiError('Perfil financiero no encontrado', { status: 404 }))
    render(<DashboardPage />)

    expect(await screen.findByRole('heading', { name: /Tu plata, con contexto/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Configurar mi cuenta/i })).toBeInTheDocument()
  })

  it('maneja el estado de API desconectada', async () => {
    api.getProfile.mockRejectedValue(new ApiError('offline', { status: 0 }))
    render(<DashboardPage />)

    expect(await screen.findByText(/No pudimos conectar con el servidor/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reintentar/i })).toBeInTheDocument()
  })

  it('tiene "Simular compra" habilitado', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    render(<DashboardPage />)
    await ready()

    const boton = screen.getByRole('button', { name: /Simular compra/i })
    expect(boton).toBeEnabled()
    expect(boton).not.toHaveAttribute('aria-disabled', 'true')
    expect(screen.queryByText(/Próximamente/i)).not.toBeInTheDocument()
  })

  it('abre el formulario de movimiento', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Registrar movimiento/i }))

    expect(await screen.findByRole('heading', { name: /Registrar movimiento/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Monto/i)).toBeInTheDocument()
  })

  it('crear un movimiento actualiza la lista y el saldo', async () => {
    const nuevaTx = makeTransaction({ amount: '20000.00', category: 'comida' })
    api.getProfile.mockResolvedValueOnce(PROFILE).mockResolvedValueOnce({
      ...PROFILE,
      current_balance: '600000.00',
    })
    api.getTransactions.mockResolvedValueOnce([]).mockResolvedValueOnce([nuevaTx])
    api.createTransaction.mockResolvedValue(nuevaTx)
    // El bloque principal muestra el saldo del resumen, que se vuelve a pedir después de
    // crear el movimiento.
    api.getDashboardSummary
      .mockResolvedValueOnce(SUMMARY)
      .mockResolvedValue({ ...SUMMARY, current_balance: '600000.00' })

    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Registrar movimiento/i }))
    await userEvent.type(screen.getByLabelText(/Monto/i), '20000')
    await userEvent.selectOptions(screen.getByLabelText(/Categoría/i), 'comida')
    await userEvent.click(screen.getByRole('button', { name: /Guardar movimiento/i }))

    // El saldo pasa a 600.000 (tarjeta) y el movimiento aparece en la lista.
    expect((await screen.findAllByText(/600\.000/)).length).toBeGreaterThan(0)
    expect(api.createTransaction).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Movimiento registrado/i)).toBeInTheDocument()
  })

  it('un error 422 muestra un mensaje comprensible', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    api.createTransaction.mockRejectedValue(
      new ApiError('Revisá los datos del formulario.', {
        status: 422,
        fieldErrors: { amount: 'El monto debe ser mayor que cero.' },
      }),
    )

    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Registrar movimiento/i }))
    await userEvent.type(screen.getByLabelText(/Monto/i), '0')
    await userEvent.selectOptions(screen.getByLabelText(/Categoría/i), 'comida')
    await userEvent.click(screen.getByRole('button', { name: /Guardar movimiento/i }))

    expect(await screen.findByText(/Revisá los datos del formulario/i)).toBeInTheDocument()
    // No se filtran detalles técnicos.
    expect(screen.queryByText(/traceback|sqlalchemy|psycopg/i)).not.toBeInTheDocument()
  })

  it('eliminar un movimiento pide confirmación', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    api.getTransactions.mockResolvedValue([makeTransaction()])

    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Eliminar movimiento de comida/i }))

    expect(await screen.findByRole('heading', { name: /Eliminar movimiento/i })).toBeInTheDocument()
    expect(screen.getByText(/¿Seguro que querés eliminar este movimiento/i)).toBeInTheDocument()
    // No se eliminó nada todavía: solo se pidió confirmación.
    expect(api.deleteTransaction).not.toHaveBeenCalled()
  })

  it('crea un compromiso', async () => {
    const nuevoCm = makeCommitment()
    api.getProfile.mockResolvedValue(PROFILE)
    api.getCommitments.mockResolvedValueOnce([]).mockResolvedValueOnce([nuevoCm])
    api.createCommitment.mockResolvedValue(nuevoCm)

    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Agregar compromiso/i }))
    await userEvent.type(screen.getByLabelText(/Nombre/i), 'Alquiler')
    await userEvent.type(screen.getByLabelText(/Monto/i), '250000')
    await userEvent.type(screen.getByLabelText(/Fecha de vencimiento/i), '2026-08-05')
    await userEvent.type(screen.getByLabelText(/Categoría/i), 'vivienda')
    await userEvent.click(screen.getByRole('button', { name: /Guardar compromiso/i }))

    expect(await screen.findByText('Alquiler')).toBeInTheDocument()
    expect(api.createCommitment).toHaveBeenCalledTimes(1)
  })

  it('cambia el estado de un compromiso a pagado', async () => {
    api.getProfile.mockResolvedValue(PROFILE)
    api.getCommitments
      .mockResolvedValueOnce([makeCommitment({ status: 'pending' })])
      .mockResolvedValueOnce([makeCommitment({ status: 'paid' })])
    api.updateCommitment.mockResolvedValue(makeCommitment({ status: 'paid' }))

    render(<DashboardPage />)
    await ready()

    await userEvent.click(screen.getByRole('button', { name: /Marcar Alquiler como pagado/i }))

    expect(await screen.findByText(/marcado como pagado/i)).toBeInTheDocument()
    expect(api.updateCommitment).toHaveBeenCalledWith('cm-1', { status: 'paid' })
    // El saldo no cambia: marcar pagado no toca el dinero.
    expect(screen.getAllByText(/620\.000/).length).toBeGreaterThan(0)
  })
})
