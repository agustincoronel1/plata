import { useState } from 'react'

import { ApiError } from '../services/api'
import { CATEGORY_LABELS, EXPENSE_CATEGORIES, suggestCategory } from '../services/categories'
import FeedbackMessage from './FeedbackMessage'
import FormField from './FormField'
import Modal from './Modal'

function todayIso() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function initialValues(transaction, prefill) {
  // `prefill.description` conserva el texto que el usuario escribió para la IA cuando
  // cae al formulario manual (fallback).
  const description = transaction?.description ?? prefill?.description ?? ''
  const type = transaction?.type ?? prefill?.type ?? 'expense'
  return {
    // `prefill.type` viene de las acciones rápidas ("Registrar gasto" / "Registrar
    // ingreso"): solo preselecciona el desplegable, que se puede cambiar igual.
    type,
    amount: transaction?.amount ?? '',
    // Un gasto nuevo arranca con la categoría que sugieren las reglas (mismas que las del
    // backend) y se puede cambiar siempre.
    category:
      transaction?.category ?? (type === 'expense' ? suggestCategory(description) : ''),
    description,
    occurred_on: transaction?.occurred_on ?? todayIso(),
    payment_method: transaction?.payment_method ?? '',
  }
}

function optionalText(value) {
  return value.trim() === '' ? null : value.trim()
}

/**
 * Formulario de alta y edición de un movimiento. El mismo componente sirve para ambos:
 * si recibe `transaction`, precarga sus datos y actúa como edición.
 */
export default function TransactionForm({ transaction, prefill, onSubmit, onClose }) {
  const isEdit = Boolean(transaction)
  const [values, setValues] = useState(() => initialValues(transaction, prefill))
  const [fieldErrors, setFieldErrors] = useState({})
  const [generalError, setGeneralError] = useState(null)
  const [busy, setBusy] = useState(false)
  // Mientras la persona no elija una categoría a mano, la sugerencia sigue a la
  // descripción. En cuanto la elige, manda ella y no se pisa nunca más.
  const [categoryChosen, setCategoryChosen] = useState(isEdit)

  function update(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  // Sugerencia vigente: un gasto la toma de la descripción; un ingreso no se sugiere.
  function suggestedCategory(type, description, current) {
    if (categoryChosen) return current
    return type === 'expense' ? suggestCategory(description) : ''
  }

  function updateDescription(value) {
    setValues((current) => ({
      ...current,
      description: value,
      category: suggestedCategory(current.type, value, current.category),
    }))
  }

  function updateType(value) {
    setValues((current) => ({
      ...current,
      type: value,
      category: suggestedCategory(value, current.description, current.category),
    }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setBusy(true)
    setFieldErrors({})
    setGeneralError(null)

    const payload = {
      type: values.type,
      amount: values.amount.trim(),
      category: values.category,
      description: optionalText(values.description),
      occurred_on: values.occurred_on,
      payment_method: optionalText(values.payment_method),
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
      title={isEdit ? 'Editar movimiento' : 'Registrar movimiento'}
      titleId="transaction-form-title"
      onClose={onClose}
    >
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormField id="tx-type" label="Tipo" error={fieldErrors.type}>
          {({ id, describedBy, invalid }) => (
            <select
              id={id}
              className="input"
              value={values.type}
              onChange={(event) => updateType(event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            >
              <option value="expense">Gasto</option>
              <option value="income">Ingreso</option>
            </select>
          )}
        </FormField>

        <FormField id="tx-amount" label="Monto" error={fieldErrors.amount}>
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
              autoFocus
            />
          )}
        </FormField>

        <FormField
          id="tx-description"
          label="Descripción (opcional)"
          error={fieldErrors.description}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="text"
              value={values.description}
              onChange={(event) => updateDescription(event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              maxLength={255}
            />
          )}
        </FormField>

        {/* Gastos: lista fija. Ingresos: texto libre, como siempre. */}
        <FormField
          id="tx-category"
          label="Categoría"
          hint={
            values.type === 'expense' && !categoryChosen
              ? 'Sugerida según la descripción. Podés cambiarla.'
              : null
          }
          error={fieldErrors.category}
        >
          {({ id, describedBy, invalid }) =>
            values.type === 'expense' ? (
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
              >
                {EXPENSE_CATEGORIES.map((name) => (
                  <option key={name} value={name}>
                    {CATEGORY_LABELS[name]}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                className="input"
                type="text"
                value={values.category}
                onChange={(event) => {
                  setCategoryChosen(true)
                  update('category', event.target.value)
                }}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                maxLength={60}
                required
              />
            )
          }
        </FormField>

        <FormField id="tx-date" label="Fecha" error={fieldErrors.occurred_on}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="date"
              max={todayIso()}
              value={values.occurred_on}
              onChange={(event) => update('occurred_on', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FormField
          id="tx-payment"
          label="Medio de pago (opcional)"
          error={fieldErrors.payment_method}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="text"
              value={values.payment_method}
              onChange={(event) => update('payment_method', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              maxLength={60}
            />
          )}
        </FormField>

        <FeedbackMessage feedback={generalError ? { type: 'error', text: generalError } : null} />

        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? 'Guardando…' : 'Guardar movimiento'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
