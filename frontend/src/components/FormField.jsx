/**
 * Campo de formulario accesible: label asociado, mensaje de error vinculado por
 * aria-describedby y estado inválido en el input.
 *
 * El input se pasa como render-prop y recibe `{ id, describedBy, invalid }` para cablear
 * la accesibilidad sin que el padre tenga que repetir los mismos ids.
 */
export default function FormField({ id, label, error, hint, children }) {
  const hintId = hint ? `${id}-hint` : null
  const errorId = error ? `${id}-error` : null
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  )
}
