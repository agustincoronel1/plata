import { useState } from 'react'

import Icon from './Icon'
import AIUsageNotice from './AIUsageNotice'
import { getAIUsage } from '../services/aiUsage'
import {
  ApiError,
  approveCopilotAction,
  chatCopilot,
  rejectCopilotAction,
} from '../services/api'
import { categoryLabel } from '../services/categories'
import { formatDate } from '../services/format'

// Ejemplos de arranque. Cada uno cae en una intención que el backend ya resuelve
// (resumen, compromisos, alta de movimiento, simulación en cuotas): no prometen nada que
// el copiloto no sepa hacer. Se envían solo si la persona los toca.
const SUGGESTIONS = [
  '¿Cuánto puedo gastar hoy?',
  '¿Qué pagos tengo antes de cobrar?',
  'Gasté 25 mil en combustible.',
  '¿Me conviene comprar una notebook en cuotas?',
]

/**
 * Copiloto financiero: conversación con el agente. Muestra el estado de análisis, cómo llegó al
 * número (en castellano, no con nombres de herramientas), la evidencia, y pausa las
 * escrituras para que la persona apruebe. Nunca muestra prompts, API key, cadena de
 * razonamiento, SQL ni campos internos del backend.
 */
export default function CopilotPanel({ onActionApplied }) {
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [pending, setPending] = useState(null) // { conversationId, action, tools, evidence }
  const [error, setError] = useState(null)
  // Cuántas consultas inteligentes quedan hoy. Es una sola cuota compartida con el resto
  // de la IA; la informa el backend en cada respuesta y acá solo se muestra.
  const [usage, setUsage] = useState(() => getAIUsage())
  const chatLocked = Boolean(pending)

  function pushMessage(role, content) {
    setMessages((current) => [...current, { role, content, id: crypto.randomUUID() }])
  }

  async function send(message) {
    if (!message.trim() || thinking || chatLocked) return
    setError(null)
    pushMessage('user', message)
    setInput('')
    setThinking(true)
    try {
      const response = await chatCopilot(message, conversationId)
      setConversationId(response.conversation_id)
      pushMessage('assistant', response.answer)
      if (response.requires_approval && response.pending_action) {
        setPending({
          conversationId: response.conversation_id,
          action: response.pending_action,
          tools: response.tools_used,
          evidence: response.evidence,
        })
      } else {
        setPending(null)
        attachDetails(response)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'El copiloto no está disponible.')
    } finally {
      // También después de un error: un 429 o un fallo del modelo cambian lo que queda.
      setUsage(getAIUsage())
      setThinking(false)
    }
  }

  function attachDetails(response) {
    // "Cómo lo resolví" es la explicación en castellano que arma el backend; los nombres
    // de las herramientas quedan en la traza del servidor, no a la vista de la persona.
    const how = response.structured_answer?.how_i_solved_it
    if (how || response.evidence?.length) {
      setMessages((current) => {
        const copy = [...current]
        const last = copy[copy.length - 1]
        if (last?.role === 'assistant') {
          last.how = how
          last.evidence = response.evidence
        }
        return copy
      })
    }
  }

  async function resolvePending(approve) {
    if (!pending || thinking) return
    setThinking(true)
    setError(null)
    try {
      const fn = approve ? approveCopilotAction : rejectCopilotAction
      const response = await fn(pending.conversationId, pending.action.action_id)
      pushMessage('assistant', response.answer)
      setPending(null)
      if (approve) onActionApplied?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No se pudo completar la acción.')
    } finally {
      setThinking(false)
    }
  }

  return (
    <section className="copilot" aria-labelledby="copilot-title">
      <div className="copilot__head">
        <span className="copilot__badge">
          <Icon name="sparkle" />
        </span>
        <div>
          <h2 className="copilot__title" id="copilot-title">
            Copiloto financiero
          </h2>
          <p className="copilot__subtitle">Responde con tus números, no con generalidades.</p>
        </div>
      </div>

      {messages.length === 0 && (
        <div className="copilot__empty">
          <p className="copilot__empty-text" id="copilot-examples-label">
            Todavía no le preguntaste nada. Podés escribirle o probar con un ejemplo:
          </p>
          <div className="copilot__suggestions" role="group" aria-labelledby="copilot-examples-label">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                className="copilot__suggestion"
                onClick={() => send(s)}
                disabled={chatLocked}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      <ol className="copilot__messages">
        {messages.map((m) => (
          <li key={m.id} className={`copilot__msg copilot__msg--${m.role}`}>
            <p className="copilot__text">{m.content}</p>
            {m.how && (
              <details className="copilot__tools">
                <summary>Cómo lo resolví</summary>
                <p>{m.how}</p>
              </details>
            )}
            {m.evidence?.length > 0 && (
              <details className="copilot__evidence">
                <summary>Evidencia ({m.evidence.length})</summary>
                <ul>
                  {m.evidence.map((e) => (
                    <li key={e.evidence_id}>
                      {e.title} · {formatDate(e.occurred_on)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
        {thinking && (
          <li className="copilot__msg copilot__msg--assistant" aria-live="polite">
            <p className="copilot__thinking">Vector está analizando tus movimientos…</p>
          </li>
        )}
      </ol>

      {pending && (
        <div className="copilot__approval" role="group" aria-label="Acción pendiente de aprobación">
          <p>
            <strong>Requiere tu aprobación:</strong> {pending.action.summary}
          </p>
          {/* La categoría ya viene resuelta en el borrador (reglas del backend, sin otra
              llamada al modelo). Se muestra para que se pueda revisar antes de aprobar. */}
          {pending.action.draft?.category && (
            <p className="copilot__pending-field">
              Categoría: <strong>{categoryLabel(pending.action.draft.category)}</strong>
            </p>
          )}
          <div className="form__actions">
            <button type="button" className="btn btn--ghost btn--small" onClick={() => resolvePending(false)} disabled={thinking}>
              Rechazar
            </button>
            <button type="button" className="btn btn--primary btn--small" onClick={() => resolvePending(true)} disabled={thinking}>
              Aprobar y registrar
            </button>
          </div>
          <p className="copilot__pending-note">Primero aproba o rechaza esta accion para seguir.</p>
        </div>
      )}

      {error && (
        <p className="copilot__error" role="alert">
          {error}
        </p>
      )}

      <AIUsageNotice usage={usage} />

      <form
        className="copilot__input"
        onSubmit={(event) => {
          event.preventDefault()
          send(input)
        }}
      >
        <label htmlFor="copilot-text" className="visually-hidden">
          Escribile al copiloto
        </label>
        <input
          id="copilot-text"
          className="input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Preguntale a Vector sobre tus finanzas…"
          disabled={thinking || chatLocked}
        />
        <button
          type="submit"
          className="icon-btn icon-btn--primary"
          aria-label="Enviar"
          disabled={thinking || chatLocked || !input.trim()}
        >
          <Icon name="send" />
        </button>
      </form>
    </section>
  )
}
