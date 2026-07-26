import { useState } from 'react'

import { ApiError, confirmAITransaction, parseAITransaction, rejectAITransaction } from '../services/api'
import AIConfidenceIndicator from './AIConfidenceIndicator'
import FeedbackMessage from './FeedbackMessage'
import FormField from './FormField'
import Modal from './Modal'

const TX_FIELDS = ['type', 'amount', 'category', 'description', 'occurred_on', 'payment_method']

function draftToValues(transaction) {
  return {
    type: transaction?.type ?? 'expense',
    amount: transaction?.amount ?? '',
    category: transaction?.category ?? '',
    description: transaction?.description ?? '',
    occurred_on: transaction?.occurred_on ?? '',
    payment_method: transaction?.payment_method ?? '',
  }
}

function optionalText(value) {
  return value.trim() === '' ? null : value.trim()
}

/**
 * "Escribilo con IA": el usuario escribe una frase, la IA propone un BORRADOR editable y
 * nada se guarda hasta que la persona confirma. Si el servicio falla, se ofrece el
 * formulario manual conservando el texto tipeado (fallback).
 */
export default function AITransactionDialog({ onRegistered, onFallback, onClose }) {
  const [phase, setPhase] = useState('input') // 'input' | 'draft'
  const [text, setText] = useState('')
  const [draft, setDraft] = useState(null)
  const [values, setValues] = useState(draftToValues(null))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [fieldErrors, setFieldErrors] = useState({})

  function update(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleParse(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const response = await parseAITransaction(text)
      setDraft(response)
      setValues(draftToValues(response.transaction))
      setPhase('draft')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No se pudo interpretar el texto.')
    } finally {
      setBusy(false)
    }
  }

  function corrections() {
    // Solo mandamos lo que el usuario cambió respecto de lo que propuso la IA.
    const original = draftToValues(draft?.transaction)
    const diff = {}
    for (const field of TX_FIELDS) {
      const value = field === 'type' || field === 'amount' ? values[field] : optionalText(values[field])
      const before = field === 'type' || field === 'amount' ? original[field] : optionalText(original[field])
      if (value !== before) diff[field] = value
    }
    return Object.keys(diff).length ? diff : null
  }

  async function handleConfirm() {
    setBusy(true)
    setError(null)
    setFieldErrors({})
    try {
      const result = await confirmAITransaction(draft.draft_id, { corrections: corrections() })
      onRegistered(result)
    } catch (err) {
      if (err instanceof ApiError && err.fieldErrors) setFieldErrors(err.fieldErrors)
      setError(err instanceof ApiError ? err.message : 'No se pudo registrar el movimiento.')
      setBusy(false)
    }
  }

  async function handleReject() {
    if (!draft?.draft_id) {
      onClose()
      return
    }
    setBusy(true)
    setError(null)
    try {
      await rejectAITransaction(draft.draft_id)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No se pudo descartar el borrador.')
      setBusy(false)
    }
  }

  const isUnknown = draft && draft.intent === 'unknown'
  const canConfirm = Boolean(
    values.type && values.amount && values.category.trim() && values.occurred_on,
  )

  return (
    <Modal title="Escribilo con IA" titleId="ai-tx-title" onClose={onClose}>
      {phase === 'input' && (
        <form className="form" onSubmit={handleParse} noValidate>
          <FormField id="ai-text" label="Contá qué pasó, en tus palabras">
            {({ id, describedBy }) => (
              <textarea
                id={id}
                className="input"
                rows={3}
                value={text}
                onChange={(event) => setText(event.target.value)}
                aria-describedby={describedBy}
                placeholder="Ej: Gasté 25 lucas ayer en nafta con débito"
                autoFocus
                required
              />
            )}
          </FormField>
          <p className="ai-hint">
            La IA solo interpreta el texto y arma un borrador. No registra nada hasta que lo
            confirmes.
          </p>
          <FeedbackMessage feedback={error ? { type: 'error', text: error } : null} />
          {error && (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => onFallback(text)}
            >
              Cargar a mano
            </button>
          )}
          <div className="form__actions">
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
              Cancelar
            </button>
            <button type="submit" className="btn btn--primary" disabled={busy || text.trim().length < 3}>
              {busy ? 'Interpretando…' : 'Interpretar'}
            </button>
          </div>
        </form>
      )}

      {phase === 'draft' && (
        <div className="form">
          <p className="ai-status" role="status">
            La IA todavía no registró nada. Revisá los datos antes de confirmar.
          </p>
          <AIConfidenceIndicator confidence={draft.confidence} />
          {draft.explanation && <p className="ai-explanation">{draft.explanation}</p>}

          {isUnknown ? (
            <>
              <p className="ai-status ai-status--warning">
                No pude interpretar un movimiento. Podés cargarlo a mano.
              </p>
              <button type="button" className="btn btn--primary" onClick={() => onFallback(text)}>
                Cargar a mano
              </button>
            </>
          ) : (
            <>
              {draft.missing_fields?.length > 0 && (
                <p className="ai-status ai-status--warning">
                  Faltan datos: {draft.missing_fields.join(', ')}. Completalos abajo.
                </p>
              )}
              {draft.ambiguities?.length > 0 && (
                <ul className="ai-ambiguities">
                  {draft.ambiguities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}

              <FormField id="ai-type" label="Tipo">
                {({ id }) => (
                  <select
                    id={id}
                    className="input"
                    value={values.type}
                    onChange={(event) => update('type', event.target.value)}
                  >
                    <option value="expense">Gasto</option>
                    <option value="income">Ingreso</option>
                  </select>
                )}
              </FormField>
              <FormField id="ai-amount" label="Monto" error={fieldErrors.amount}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    type="number"
                    step="0.01"
                    min="0"
                    value={values.amount}
                    onChange={(event) => update('amount', event.target.value)}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                  />
                )}
              </FormField>
              <FormField id="ai-category" label="Categoría" error={fieldErrors.category}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    value={values.category}
                    onChange={(event) => update('category', event.target.value)}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    maxLength={60}
                  />
                )}
              </FormField>
              <FormField id="ai-description" label="Descripción" error={fieldErrors.description}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    value={values.description}
                    onChange={(event) => update('description', event.target.value)}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    maxLength={255}
                  />
                )}
              </FormField>
              <FormField id="ai-payment-method" label="Medio de pago" error={fieldErrors.payment_method}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    value={values.payment_method}
                    onChange={(event) => update('payment_method', event.target.value)}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    maxLength={60}
                  />
                )}
              </FormField>
              <FormField id="ai-date" label="Fecha" error={fieldErrors.occurred_on}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    type="date"
                    value={values.occurred_on}
                    onChange={(event) => update('occurred_on', event.target.value)}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                  />
                )}
              </FormField>

              <FeedbackMessage feedback={error ? { type: 'error', text: error } : null} />

              <div className="form__actions">
                <button type="button" className="btn btn--ghost" onClick={handleReject} disabled={busy}>
                  Descartar
                </button>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={handleConfirm}
                  disabled={busy || !canConfirm}
                >
                  {busy ? 'Registrando…' : 'Confirmar y registrar'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  )
}
