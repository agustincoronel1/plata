import { useEffect, useRef } from 'react'

import Icon from './Icon'

/**
 * Contenedor del copiloto. En desktop es una tarjeta más del dashboard; en mobile se
 * comporta como una hoja inferior a pantalla casi completa que se abre desde el botón
 * flotante o desde la navegación.
 *
 * El panel nunca se desmonta: la conversación, la acción pendiente y el estado del agente
 * sobreviven a abrir y cerrar la hoja.
 */
export default function CopilotDock({ open, onClose, children }) {
  const closeRef = useRef(null)

  // El foco se mueve una sola vez, al abrir: mientras la hoja está abierta la persona
  // escribe en el copiloto sin que nada se lo robe. En desktop el botón está oculto por
  // CSS y el foco simplemente no se mueve.
  useEffect(() => {
    if (open) closeRef.current?.focus()
  }, [open])

  // En mobile la hoja se cierra con Escape, igual que los diálogos.
  useEffect(() => {
    if (!open) return undefined

    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  return (
    <div id="copiloto" className="copilot-dock" data-open={open ? 'true' : 'false'}>
      <div className="copilot-dock__scrim" aria-hidden="true" onClick={onClose} />
      <div className="copilot-dock__panel">
        <button
          type="button"
          ref={closeRef}
          className="icon-btn copilot-dock__close"
          onClick={onClose}
          aria-label="Cerrar el copiloto"
        >
          <Icon name="close" />
        </button>
        {children}
      </div>
    </div>
  )
}
