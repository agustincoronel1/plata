import Icon from './Icon'

/**
 * Estado vacío de una sección: icono, título, explicación breve y, opcionalmente, las
 * acciones que ya existen para salir de ese estado. No hace fetch ni decide si la lista
 * está vacía: eso lo resuelve el componente que ya recibe los datos.
 *
 * Las acciones se pasan como children (botones reales) para no duplicar lógica ni
 * inventar un formulario nuevo.
 */
export default function EmptyState({ icon = 'sparkle', title, description, note, children }) {
  return (
    <div className="empty">
      <span className="empty__icon">
        <Icon name={icon} />
      </span>
      <h3 className="empty__title">{title}</h3>
      <p className="empty__text">{description}</p>
      {note && <p className="empty__note">{note}</p>}
      {children && <div className="empty__actions">{children}</div>}
    </div>
  )
}
