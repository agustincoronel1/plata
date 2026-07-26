import { useEffect, useRef } from 'react'

/**
 * Diálogo accesible sobre el elemento nativo <dialog>.
 *
 * Usa showModal(), así que el navegador aporta gratis el foco atrapado dentro del panel,
 * el cierre con Escape y el fondo inerte. Al cerrarse devuelve el foco al elemento que lo
 * abrió. El cierre siempre pasa por onClose, que desmonta el diálogo desde el padre.
 */
export default function Modal({ title, titleId, onClose, children }) {
  const ref = useRef(null)

  useEffect(() => {
    const opener = document.activeElement
    const dialog = ref.current
    dialog.showModal()

    return () => {
      // Devolver el foco a quien abrió el diálogo cuando este se desmonta.
      if (opener instanceof HTMLElement) {
        opener.focus()
      }
    }
  }, [])

  // Escape dispara el evento 'cancel'; lo controlamos nosotros para desmontar limpio.
  function handleCancel(event) {
    event.preventDefault()
    onClose()
  }

  // Clic sobre el fondo (el propio <dialog>, no el panel interior) cierra el diálogo.
  function handleClick(event) {
    if (event.target === ref.current) {
      onClose()
    }
  }

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby={titleId}
      onCancel={handleCancel}
      onClick={handleClick}
    >
      <div className="modal__panel">
        <div className="modal__header">
          <h2 className="modal__title" id={titleId}>
            {title}
          </h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Cerrar">
            <span aria-hidden="true">×</span>
          </button>
        </div>
        {children}
      </div>
    </dialog>
  )
}
