import { useState } from 'react'

import { ApiError } from '../services/api'
import { CATEGORY_LABELS, EXPENSE_CATEGORIES, suggestCategory } from '../services/categories'
import FeedbackMessage from './FeedbackMessage'
import FormField from './FormField'
import Modal from './Modal'

function initialValues(commitment) {
  const name = commitment?.name ?? ''
  return {
    name,
    amount: commitment?.amount ?? '',
    due_date: commitment?.due_date ?? '',
    category: commitment?.category ?? suggestCategory(name),
    is_recurring: commitment?.is_recurring ?? false,
  }
}

/**
 * Formulario de alta y edición de un compromiso. Al crear, el status siempre es pending:
 * el formulario no lo expone. El estado (pagado / cancelado) se cambia desde la lista.
 */
export default function CommitmentForm({ commitment, onSubmit, onClose }) {
  const isEdit = Boolean(commitment)
  const [values, setValues] = useState(() => initialValues(commitment))
  const [fieldErrors, setFieldErrors] = useState({})
  const [generalError, setGeneralError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [categoryChosen, setCategoryChosen] = useState(isEdit)

  function update(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  function updateName(value) {
    setValues((current) => ({
      ...current,
      name: value,
      category: categoryChosen ? current.category : suggestCategory(value),
    }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setBusy(true)
    setFieldErrors({})
    setGeneralError(null)

    const payload = {
      name: values.name,
      amount: values.amount.trim(),
      due_date: values.due_date,
      category: values.category,
      is_recurring: values.is_recurring,
    }

    try {
      await onSubmit(payload)
    } catch (error) {
      if (error instanceof ApiError && error.fieldErrors) {
        setFieldErrors(error.fieldErrors)
      }
      setGeneralError(error instanceof ApiError ? error.message : 'No se pudo guardar.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={isEdit ? 'Editar compromiso' : 'Agregar compromiso'}
      titleId="commitment-form-title"
      onClose={onClose}
    >
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormField id="cm-name" label="Nombre" error={fieldErrors.name}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="text"
              value={values.name}
              onChange={(event) => updateName(event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              maxLength={120}
              required
              autoFocus
            />
          )}
        </FormField>

        <FormField id="cm-amount" label="Monto" error={fieldErrors.amount}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              value={values.amount}
              onChange={(event) => update('amount', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FormField id="cm-due" label="Fecha de vencimiento" error={fieldErrors.due_date}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="date"
              value={values.due_date}
              onChange={(event) => update('due_date', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FormField
          id="cm-category"
          label="Categoría"
          hint={!categoryChosen ? 'Sugerida según el nombre. Podés cambiarla.' : null}
          error={fieldErrors.category}
        >
          {({ id, describedBy, invalid }) => (
            <select
              id={id}
              className="input"
              value={values.category}
              onChange={(event) => {
                setCategoryChosen(true)
                update('category', event.target.value)
              }}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            >
              {EXPENSE_CATEGORIES.map((name) => (
                <option key={name} value={name}>
                  {CATEGORY_LABELS[name]}
                </option>
              ))}
            </select>
          )}
        </FormField>

        <div className="field field--check">
          <input
            id="cm-recurring"
            className="checkbox"
            type="checkbox"
            checked={values.is_recurring}
            onChange={(event) => update('is_recurring', event.target.checked)}
          />
          <label className="field__label" htmlFor="cm-recurring">
            Es recurrente
          </label>
        </div>

        <FeedbackMessage feedback={generalError ? { type: 'error', text: generalError } : null} />

        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? 'Guardando…' : 'Guardar compromiso'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
