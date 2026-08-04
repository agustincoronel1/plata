/**
 * Aviso de cuota diaria de consultas inteligentes a punto de agotarse.
 *
 * Aparece solo cuando quedan pocas —el umbral lo decide el backend y viaja con la
 * respuesta— y habla de una sola cuota, porque eso es lo que hay: el copiloto y la
 * interpretación de movimientos consumen el mismo cupo diario de la cuenta.
 *
 * Es deliberadamente discreto y no es una burbuja de conversación: no entra en el
 * historial del chat, no interrumpe y no bloquea nada. Alguien que usa Plata dos veces al
 * día nunca se entera de que hay un límite.
 */

export default function AIUsageNotice({ usage }) {
  // Se muestra desde el umbral hacia abajo, incluido el cero: quedarse sin cuota es
  // justamente lo que hay que avisar.
  if (!usage || usage.remaining > usage.warnAt) {
    return null
  }

  let texto
  if (usage.remaining === 0) {
    texto = 'Usaste tu última consulta inteligente de hoy.'
  } else if (usage.remaining === 1) {
    texto = 'Te queda 1 consulta inteligente por hoy.'
  } else {
    texto = `Te quedan ${usage.remaining} consultas inteligentes por hoy.`
  }

  return (
    <p className="ai-usage-notice" role="status" aria-live="polite">
      {texto} Se renueva mañana.
    </p>
  )
}
