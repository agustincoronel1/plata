import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import CommitmentForm from './CommitmentForm'
import CommitmentList from './CommitmentList'

function renderForm(props = {}) {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<CommitmentForm onSubmit={onSubmit} onClose={vi.fn()} {...props} />)
  return onSubmit
}

const categoria = () => screen.getByLabelText(/Categoría/i)

describe('CommitmentForm: categoría', () => {
  it('muestra un selector con la lista fija al crear', () => {
    renderForm()

    expect(categoria().tagName).toBe('SELECT')
    expect(screen.getByRole('option', { name: 'Servicios' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Otros' })).toBeInTheDocument()
  })

  it('propone una categoría a partir del nombre', async () => {
    renderForm()

    await userEvent.type(screen.getByLabelText(/Nombre/i), 'Internet')

    expect(categoria()).toHaveValue('servicios')
    expect(screen.getByText(/Sugerida según el nombre/i)).toBeInTheDocument()
  })

  it('lo que elige la persona no se pisa después', async () => {
    renderForm()

    await userEvent.selectOptions(categoria(), 'ocio')
    await userEvent.type(screen.getByLabelText(/Nombre/i), 'Internet')

    expect(categoria()).toHaveValue('ocio')
  })

  it('envía category al crear', async () => {
    const onSubmit = renderForm()

    await userEvent.type(screen.getByLabelText(/Nombre/i), 'Internet')
    await userEvent.type(screen.getByLabelText(/Monto/i), '30000')
    await userEvent.type(screen.getByLabelText(/Fecha de vencimiento/i), '2026-08-10')
    await userEvent.click(screen.getByRole('button', { name: /Guardar compromiso/i }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Internet', amount: '30000', category: 'servicios' }),
    )
  })

  it('al editar conserva la categoría guardada', () => {
    renderForm({
      commitment: {
        id: 'cm-1',
        name: 'Internet',
        amount: '30000.00',
        due_date: '2026-08-10',
        category: 'ocio',
        is_recurring: false,
      },
    })

    expect(categoria()).toHaveValue('ocio')
  })
})

describe('CommitmentList: categoría', () => {
  it('muestra la categoría con etiqueta legible', () => {
    render(
      <CommitmentList
        commitments={[
          {
            id: 'cm-1',
            name: 'Internet',
            amount: '30000.00',
            due_date: '2026-08-10',
            category: 'servicios',
            status: 'pending',
            is_recurring: false,
          },
        ]}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onChangeStatus={vi.fn()}
        onCreate={vi.fn()}
      />,
    )

    expect(screen.getByText(/Servicios/i)).toBeInTheDocument()
  })
})
