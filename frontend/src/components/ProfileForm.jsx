import { useState } from 'react'

import { ApiError } from '../services/api'
import FeedbackMessage from './FeedbackMessage'
import FormField from './FormField'
import Modal from './Modal'

// El dinero se maneja como texto en el input y se envía como string: nunca pasa por un
// número flotante en el frontend.
function moneyOrZero(value) {
  return value.trim() === '' ? '0' : value.trim()
}

function initialValues(profile) {
  return {
    name: profile?.name ?? '',
    current_balance: profile?.current_balance ?? '',
    next_income_amount: profile?.next_income_amount ?? '',
    next_income_date: profile?.next_income_date ?? '',
    protected_amount: profile?.protected_amount ?? '',
    safety_buffer: profile?.safety_buffer ?? '',
  }
}

/**
 * Formulario del perfil financiero. Sirve para editar la situación y también para la
 * configuración inicial cuando todavía no hay perfil (mismo formulario, distinto título).
 */
export default function ProfileForm({ profile, isSetup = false, onSubmit, onClose }) {
  const [values, setValues] = useState(() => initialValues(profile))
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
      name: values.name,
      currency: 'ARS',
      current_balance: moneyOrZero(values.current_balance),
      next_income_amount: moneyOrZero(values.next_income_amount),
      next_income_date: values.next_income_date || null,
      protected_amount: moneyOrZero(values.protected_amount),
      safety_buffer: moneyOrZero(values.safety_buffer),
    }

    try {
      await onSubmit(payload)
      // Éxito: el padre cierra el diálogo y actualiza la pantalla.
    } catch (error) {
      if (error instanceof ApiError && error.fieldErrors) {
        setFieldErrors(error.fieldErrors)
      }
      setGeneralError(error instanceof ApiError ? error.message : 'No se pudo guardar.')
      setBusy(false)
    }
  }

  const title = isSetup ? 'Configurá tu situación' : 'Editar situación'

  return (
    <Modal title={title} titleId="profile-form-title" onClose={onClose}>
      {isSetup && (
        <p className="modal__text">
          Todavía no tenés un perfil cargado. Completá estos datos para empezar.
        </p>
      )}
      <form className="form" onSubmit={handleSubmit} noValidate>
        <FormField id="profile-name" label="Nombre" error={fieldErrors.name}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="text"
              value={values.name}
              onChange={(event) => update('name', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              maxLength={120}
              required
              autoFocus
            />
          )}
        </FormField>

        <FormField
          id="profile-balance"
          label="Saldo actual"
          hint="Puede ser negativo si estás en rojo."
          error={fieldErrors.current_balance}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              inputMode="decimal"
              value={values.current_balance}
              onChange={(event) => update('current_balance', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </FormField>

        <FormField
          id="profile-income"
          label="Próximo ingreso"
          error={fieldErrors.next_income_amount}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              value={values.next_income_amount}
              onChange={(event) => update('next_income_amount', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </FormField>

        <FormField
          id="profile-income-date"
          label="Fecha del próximo ingreso"
          error={fieldErrors.next_income_date}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="date"
              value={values.next_income_date}
              onChange={(event) => update('next_income_date', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </FormField>

        <FormField
          id="profile-protected"
          label="Dinero protegido"
          hint="Reserva que Vector no considera disponible."
          error={fieldErrors.protected_amount}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              value={values.protected_amount}
              onChange={(event) => update('protected_amount', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </FormField>

        <FormField
          id="profile-buffer"
          label="Margen de seguridad"
          error={fieldErrors.safety_buffer}
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="input"
              type="number"
              step="0.01"
              min="0"
              inputMode="decimal"
              value={values.safety_buffer}
              onChange={(event) => update('safety_buffer', event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </FormField>

        <p className="field__hint">Moneda: ARS (única disponible por ahora).</p>

        <FeedbackMessage feedback={generalError ? { type: 'error', text: generalError } : null} />

        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? 'Guardando…' : 'Guardar'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
