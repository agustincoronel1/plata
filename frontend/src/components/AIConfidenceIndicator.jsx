/**
 * Indica la confianza de la interpretación de la IA con TEXTO (no solo color): el nivel se
 * nombra explícitamente ("Confianza alta/media/baja") para no depender de la vista.
 */
function level(confidence) {
  const value = Number(confidence)
  if (Number.isNaN(value)) return { label: 'desconocida', tone: 'unknown' }
  if (value >= 0.85) return { label: 'alta', tone: 'high' }
  if (value >= 0.6) return { label: 'media', tone: 'medium' }
  return { label: 'baja', tone: 'low' }
}

export default function AIConfidenceIndicator({ confidence }) {
  const { label, tone } = level(confidence)
  return (
    <p className={`ai-confidence ai-confidence--${tone}`}>
      <span className="ai-confidence__dot" aria-hidden="true" />
      Confianza de la IA: <strong>{label}</strong>
    </p>
  )
}
