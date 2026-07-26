import Modal from './Modal'

/**
 * Confirmación accesible previa a una acción destructiva (eliminar).
 *
 * Reutiliza Modal: foco atrapado, Escape para cancelar y botones reales. El botón que
 * confirma queda deshabilitado mientras la operación está en curso.
 */
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Eliminar',
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}) {
  return (
    <Modal title={title} titleId="confirm-dialog-title" onClose={onCancel}>
      <p className="modal__text">{message}</p>
      {error && (
        <p className="feedback feedback--error" role="alert">
          <span className="feedback__label">Error: </span>
          {error}
        </p>
      )}
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
        <button type="button" className="btn btn--danger" onClick={onConfirm} disabled={busy}>
          {busy ? 'Eliminando…' : confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
