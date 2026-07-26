/**
 * Mensaje de estado de una operación (éxito o error).
 *
 * Un error se anuncia con role="alert" (interrumpe); un éxito con role="status" (educado).
 * El estilo nunca depende solo del color: lleva un prefijo textual.
 */
export default function FeedbackMessage({ feedback }) {
  if (!feedback) {
    return null
  }

  const { type, text } = feedback
  const isError = type === 'error'

  return (
    <p
      className={`feedback feedback--${isError ? 'error' : 'success'}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
    >
      <span className="feedback__label">{isError ? 'Error: ' : 'Listo: '}</span>
      {text}
    </p>
  )
}
