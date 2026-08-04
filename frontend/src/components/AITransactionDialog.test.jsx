import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AITransactionDialog from './AITransactionDialog'
import * as api from '../services/api'
import { makeParseDraft } from '../test/fixtures'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    parseAITransaction: vi.fn(),
    confirmAITransaction: vi.fn(),
    rejectAITransaction: vi.fn(),
  }
})

const { ApiError } = api

beforeEach(() => vi.clearAllMocks())
afterEach(() => vi.clearAllMocks())

function renderDialog(props = {}) {
  return render(
    <AITransactionDialog
      onRegistered={props.onRegistered ?? vi.fn()}
      onFallback={props.onFallback ?? vi.fn()}
      onClose={props.onClose ?? vi.fn()}
    />,
  )
}

describe('AITransactionDialog', () => {
  it('interpreta el texto y muestra un borrador que aclara que no registró nada', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Contá qué pasó/i), 'Gasté 25 lucas ayer en nafta')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))

    expect(await screen.findByText(/todavía no registró nada/i)).toBeInTheDocument()
    expect(screen.getByText(/Confianza de la IA/i)).toBeInTheDocument()
    expect(screen.getByDisplayValue('25000.00')).toBeInTheDocument()
  })

  it('muestra la categoría que resolvió el backend y deja cambiarla', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    api.confirmAITransaction.mockResolvedValue({ draft_id: 'x', transaction: { id: 't1' } })
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas ayer en nafta')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))

    const categoria = await screen.findByLabelText(/Categoría/i)
    expect(categoria.tagName).toBe('SELECT')
    expect(categoria).toHaveValue('transporte')

    await user.selectOptions(categoria, 'ocio')
    await user.click(screen.getByRole('button', { name: /Confirmar y registrar/i }))

    expect(api.confirmAITransaction).toHaveBeenCalledWith(
      '99999999-9999-4999-8999-999999999999',
      { corrections: { category: 'ocio' } },
    )
  })

  it('confirma el borrador y avisa al padre', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    api.confirmAITransaction.mockResolvedValue({ draft_id: 'x', transaction: { id: 't1' } })
    const onRegistered = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onRegistered })

    await user.type(screen.getByLabelText(/Contá qué pasó/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))
    await user.click(await screen.findByRole('button', { name: /Confirmar y registrar/i }))

    expect(api.confirmAITransaction).toHaveBeenCalledOnce()
    expect(onRegistered).toHaveBeenCalledOnce()
  })

  it('descarta el borrador llamando al endpoint', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    api.rejectAITransaction.mockResolvedValue(null)
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onClose })

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))
    await user.click(await screen.findByRole('button', { name: /Descartar/i }))

    expect(api.rejectAITransaction).toHaveBeenCalledWith('99999999-9999-4999-8999-999999999999')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('permite editar descripción y medio de pago', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    api.confirmAITransaction.mockResolvedValue({ draft_id: 'x', transaction: { id: 't1' } })
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))
    await user.clear(await screen.findByLabelText(/Descripción/i))
    await user.type(screen.getByLabelText(/Descripción/i), 'Nafta ruta')
    await user.clear(screen.getByLabelText(/Medio de pago/i))
    await user.type(screen.getByLabelText(/Medio de pago/i), 'Crédito')
    await user.click(screen.getByRole('button', { name: /Confirmar y registrar/i }))

    expect(api.confirmAITransaction).toHaveBeenCalledWith(
      '99999999-9999-4999-8999-999999999999',
      { corrections: { description: 'Nafta ruta', payment_method: 'Crédito' } },
    )
  })

  it('no confirma un draft incompleto', async () => {
    api.parseAITransaction.mockResolvedValue(
      makeParseDraft({
        transaction: {
          type: 'expense',
          amount: '',
          category: '',
          description: null,
          occurred_on: '',
          payment_method: null,
        },
        missing_fields: ['amount', 'category', 'occurred_on'],
        is_confirmable: false,
      }),
    )
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté algo')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))
    const button = await screen.findByRole('button', { name: /Confirmar y registrar/i })

    expect(button).toBeDisabled()
    await user.click(button)
    expect(api.confirmAITransaction).not.toHaveBeenCalled()
  })

  it('no permite un segundo envío mientras la IA está interpretando', async () => {
    let resolveParse
    api.parseAITransaction.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveParse = resolve
        }),
    )
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))

    // Estado de carga visible y botón bloqueado mientras la petición sigue en curso.
    const pendingButton = await screen.findByRole('button', { name: /Interpretando/i })
    expect(pendingButton).toBeDisabled()
    await user.click(pendingButton)
    expect(api.parseAITransaction).toHaveBeenCalledTimes(1)

    resolveParse(makeParseDraft())
    expect(await screen.findByText(/todavía no registró nada/i)).toBeInTheDocument()
  })

  it('muestra el mensaje de timeout de la IA y no el de backend apagado', async () => {
    api.parseAITransaction.mockRejectedValue(
      new ApiError('La IA tardó más de lo esperado. Intentá nuevamente.', {
        status: 0,
        timeout: true,
      }),
    )
    const user = userEvent.setup()
    renderDialog()

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/La IA tardó más de lo esperado/i)
    expect(screen.queryByText(/Revisá que el backend esté activo/i)).not.toBeInTheDocument()
    // El estado se restaura: se puede volver a intentar (sin reintento automático).
    expect(screen.getByRole('button', { name: /Interpretar/i })).toBeEnabled()
    expect(api.parseAITransaction).toHaveBeenCalledTimes(1)
  })

  it('no permite confirmar dos veces el mismo borrador', async () => {
    api.parseAITransaction.mockResolvedValue(makeParseDraft())
    let resolveConfirm
    api.confirmAITransaction.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveConfirm = resolve
        }),
    )
    const onRegistered = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onRegistered })

    await user.type(screen.getByLabelText(/Cont/i), 'Gasté 25 lucas')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))
    await user.click(await screen.findByRole('button', { name: /Confirmar y registrar/i }))

    const pendingButton = await screen.findByRole('button', { name: /Registrando/i })
    expect(pendingButton).toBeDisabled()
    await user.click(pendingButton)
    expect(api.confirmAITransaction).toHaveBeenCalledTimes(1)

    resolveConfirm({ draft_id: 'x', transaction: { id: 't1' } })
    await waitFor(() => expect(onRegistered).toHaveBeenCalledOnce())
  })

  it('ofrece el formulario manual si la interpretación falla (fallback)', async () => {
    api.parseAITransaction.mockRejectedValue(new ApiError('El copiloto no está disponible', { status: 503 }))
    const onFallback = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onFallback })

    await user.type(screen.getByLabelText(/Contá qué pasó/i), 'algo raro')
    await user.click(screen.getByRole('button', { name: /Interpretar/i }))

    const manual = await screen.findByRole('button', { name: /Cargar a mano/i })
    await user.click(manual)
    expect(onFallback).toHaveBeenCalledWith('algo raro')
  })
})
