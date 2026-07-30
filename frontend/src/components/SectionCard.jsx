/**
 * Superficie de una sección del dashboard: título, subtítulo opcional, acciones del
 * encabezado y contenido. Existe para no repetir la misma tarjeta cinco veces.
 */
export default function SectionCard({
  id,
  title,
  titleId,
  subtitle,
  action,
  className,
  children,
}) {
  return (
    <section
      id={id}
      className={className ? `card ${className}` : 'card'}
      aria-labelledby={titleId}
    >
      <div className="card__head">
        <div className="card__heading">
          <h2 className="card__title" id={titleId}>
            {title}
          </h2>
          {subtitle && <p className="card__subtitle">{subtitle}</p>}
        </div>
        {action && <div className="card__actions">{action}</div>}
      </div>
      {children}
    </section>
  )
}
