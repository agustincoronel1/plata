import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import TransactionForm from './TransactionForm'

function renderForm(props = {}) {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<TransactionForm onSubmit={onSubmit} onClose={vi.fn()} {...props} />)
  return onSubmit
}

const categoria = () => screen.getByLabelText(/Categoría/i)

describe('TransactionForm: categoría', () => {
  it('un gasto elige la categoría de una lista fija', () => {
    renderForm()
    expect(categoria().tagName).toBe('SELECT')
    expect(screen.getByRole('option', { name: 'Transporte' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Educación' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Otros' })).toBeInTheDocument()
  })

  it('propone una categoría a partir de la descripción', async () => {
    renderForm()
    await userEvent.type(screen.getByLabelText(/Descripción/i), 'Carga de nafta')
    expect(categoria()).toHaveValue('transporte')
    expect(screen.getByText(/Sugerida según la descripción/i)).toBeInTheDocument()
  })

  it('lo que elige la persona le gana a la sugerencia', async () => {
    renderForm()
    await userEvent.selectOptions(categoria(), 'ocio')
    await userEvent.type(screen.getByLabelText(/Descripción/i), 'Carga de nafta')
    expect(categoria()).toHaveValue('ocio')
  })

  it('guarda la categoría junto con el movimiento', async () => {
    const onSubmit = renderForm()
    await userEvent.type(screen.getByLabelText(/Monto/i), '15000')
    await userEvent.type(screen.getByLabelText(/Descripción/i), 'Nafta')
    await userEvent.click(screen.getByRole('button', { name: /Guardar movimiento/i }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'expense', amount: '15000', category: 'transporte' }),
    )
  })

  it('un ingreso conserva la categoría de texto libre', async () => {
    renderForm({ prefill: { type: 'income' } })
    expect(categoria().tagName).toBe('INPUT')
    await userEvent.type(categoria(), 'sueldo')
    expect(categoria()).toHaveValue('sueldo')
  })

  it('al editar respeta la categoría guardada', async () => {
    renderForm({
      transaction: {
        id: 'tx-1',
        type: 'expense',
        amount: '20000.00',
        category: 'ocio',
        description: 'Nafta',
        occurred_on: '2026-07-20',
        payment_method: null,
      },
    })
    expect(categoria()).toHaveValue('ocio')
  })
})
