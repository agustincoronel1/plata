import { AI_USAGE_KINDS } from '../services/aiUsage'

/**
 * Aviso de cuota diaria de IA a punto de agotarse.
 *
 * Aparece solo cuando quedan pocos usos —el umbral lo decide el backend y viaja con la
 * respuesta— y nombra la operación concreta: "consultas al copiloto" e "interpretaciones"
 * no son lo mismo para quien lo lee, y decir cuál se está agotando evita que parezca que
 * Plata entera se queda sin funcionar.
 *
 * Es deliberadamente discreto y no es una burbuja de conversación: no entra en el
 * historial del chat, no interrumpe y no bloquea nada. Alguien que usa Plata dos veces al
 * día nunca se entera de que hay un límite.
 */

const COPY = {
  [AI_USAGE_KINDS.copilotChat]: {
    plural: (n) => `Te quedan ${n} consultas al copiloto por hoy.`,
    singular: 'Te queda 1 consulta al copiloto por hoy.',
    none: 'Usaste tu última consulta al copiloto de hoy.',
  },
  [AI_USAGE_KINDS.transactionParse]: {
    plural: (n) => `Te quedan ${n} interpretaciones con IA por hoy.`,
    singular: 'Te queda 1 interpretación con IA por hoy.',
    none: 'Usaste tu última interpretación con IA de hoy.',
  },
}

export default function AIUsageNotice({ usage }) {
  const copy = usage ? COPY[usage.kind] : null
  // Se muestra desde el umbral hacia abajo, incluido el cero: quedarse sin cuota es
  // justamente lo que hay que avisar.
  if (!copy || usage.remaining > usage.warnAt) {
    return null
  }

  let texto
  if (usage.remaining === 0) {
    texto = copy.none
  } else if (usage.remaining === 1) {
    texto = copy.singular
  } else {
    texto = copy.plural(usage.remaining)
  }

  return (
    <p className="ai-usage-notice" role="status" aria-live="polite">
      {texto} Se renueva mañana.
    </p>
  )
}
