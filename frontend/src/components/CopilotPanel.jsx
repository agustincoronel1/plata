import { useState } from 'react'

import {
  ApiError,
  approveCopilotAction,
  chatCopilot,
  rejectCopilotAction,
} from '../services/api'

const SUGGESTIONS = [
  '¿Cuánto puedo gastar hoy?',
  'Explicame mi disponible',
  '¿Qué pagos tengo antes de cobrar?',
  'Buscar gastos parecidos',
]

/**
 * Copiloto financiero: conversación con el agente. Muestra "Pensando…", qué herramientas
 * usó (en una sección técnica discreta), la evidencia, y pausa las escrituras para que la
 * persona apruebe. Nunca muestra prompts, API key, cadena de razonamiento ni SQL.
 */
export default function CopilotPanel({ onActionApplied }) {
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [pending, setPending] = useState(null) // { conversationId, action, tools, evidence }
  const [error, setError] = useState(null)
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
      setThinking(false)
    }
  }

  function attachDetails(response) {
    if (response.tools_used?.length || response.evidence?.length) {
      setMessages((current) => {
        const copy = [...current]
        const last = copy[copy.length - 1]
        if (last?.role === 'assistant') {
          last.tools = response.tools_used
          last.evidence = response.evidence
        }
        return copy
      })
    }
  }

  async function resolvePending(approve) {
    if (!pending) return
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
      <div className="section-head">
        <h2 className="section-head__title" id="copilot-title">
          Copiloto financiero
        </h2>
      </div>

      {messages.length === 0 && (
        <div className="copilot__suggestions">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className="btn btn--ghost btn--small"
              onClick={() => send(s)}
              disabled={chatLocked}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <ol className="copilot__messages">
        {messages.map((m) => (
          <li key={m.id} className={`copilot__msg copilot__msg--${m.role}`}>
            <p>{m.content}</p>
            {m.tools?.length > 0 && (
              <details className="copilot__tools">
                <summary>Cómo lo resolví</summary>
                <ul>
                  {m.tools.map((t, i) => (
                    <li key={`${t.name}-${i}`}>{t.name}</li>
                  ))}
                </ul>
              </details>
            )}
            {m.evidence?.length > 0 && (
              <details className="copilot__evidence">
                <summary>Evidencia ({m.evidence.length})</summary>
                <ul>
                  {m.evidence.map((e) => (
                    <li key={e.evidence_id}>
                      {e.title} · {e.occurred_on} <span className="copilot__method">({e.retrieval_method})</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
        {thinking && (
          <li className="copilot__msg copilot__msg--assistant" aria-live="polite">
            <p className="copilot__thinking">Pensando…</p>
          </li>
        )}
      </ol>

      {pending && (
        <div className="copilot__approval" role="group" aria-label="Acción pendiente de aprobación">
          <p>
            <strong>Requiere tu aprobación:</strong> {pending.action.summary}
          </p>
          <div className="form__actions">
            <button type="button" className="btn btn--ghost" onClick={() => resolvePending(false)} disabled={thinking}>
              Rechazar
            </button>
            <button type="button" className="btn btn--primary" onClick={() => resolvePending(true)} disabled={thinking}>
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
          placeholder="Preguntá algo sobre tu plata…"
          disabled={thinking || chatLocked}
        />
        <button type="submit" className="btn btn--primary" disabled={thinking || chatLocked || !input.trim()}>
          Enviar
        </button>
      </form>
    </section>
  )
}
