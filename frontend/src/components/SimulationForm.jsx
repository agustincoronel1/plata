import { useState } from 'react'

import { ApiError } from '../services/api'
import FeedbackMessage from './FeedbackMessage'
import FormField from './FormField'
import Modal from './Modal'

function todayIso() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/**
 * Formulario para simular una compra en cuotas. El total es el costo final financiado;
 * Plata no calcula intereses. La proyección la hace el backend.
 */
export default function SimulationForm({ onSubmit, onClose }) {
  const [values, setValues] = useState({
    purchase_name: '',
    total_amount: '',
    installments: '3',
    first_installment_date: todayIso(),
  })
  const [fieldErrors, setFieldErrors] = useState({})
  const [generalError, setGeneralError] = useState(null)
  const [busy, setBusy] = useState(false)

  function update(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setBusy(true)
    setFieldErrors({})
    setGeneralError(null)

    const payload = {
      purchase_name: values.purchase_name,
      total_amount: values.total_amount.trim(),
      installments: Number(values.installments),
      first_installment_date: values.first_installment_date,
    }

    try {
      await onSubmit(payload)
    } catch (error) {
      if (error instanceof ApiError && error.fieldErrors) {
        setFieldErrors(error.fieldErrors)
      }
      setGeneralError(error instanceof ApiError ? error.message : 'No se pudo simular.')
      setBusy(false)
    }
  }

  return (
    <Modal title="Simular compra" titleId="simulation-form-title" onClose={onClose}>
      <p className="modal__text">
        Ingresá el total final financiado. Plata no calcula intereses: usa el monto que ya
        vas a pagar.
      </p>
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormField id="sim-name" label="Nombre de la compra" error={fieldErrors.purchase_name}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="text"
              value={values.purchase_name}
              onChange={(event) => update('purchase_name', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              maxLength={120}
              required
              autoFocus
            />
          )}
        </FormField>

        <FormField id="sim-total" label="Total final a pagar" error={fieldErrors.total_amount}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              value={values.total_amount}
              onChange={(event) => update('total_amount', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FormField
          id="sim-installments"
          label="Cantidad de cuotas (1 a 24)"
          error={fieldErrors.installments}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              min="1"
              max="24"
              step="1"
              inputMode="numeric"
              value={values.installments}
              onChange={(event) => update('installments', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FormField
          id="sim-date"
          label="Fecha de la primera cuota"
          error={fieldErrors.first_installment_date}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="date"
              min={todayIso()}
              value={values.first_installment_date}
              onChange={(event) => update('first_installment_date', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              required
            />
          )}
        </FormField>

        <FeedbackMessage feedback={generalError ? { type: 'error', text: generalError } : null} />

        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? 'Simulando…' : 'Simular'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
