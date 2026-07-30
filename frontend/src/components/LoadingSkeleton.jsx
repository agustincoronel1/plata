/**
 * Esqueleto de carga del dashboard. Es puramente decorativo: el estado se anuncia con el
 * texto que lo acompaña, no con estas piezas.
 */
export default function LoadingSkeleton() {
  return (
    <div className="skeleton-stack" aria-hidden="true">
      <div className="skeleton skeleton--hero" />
      <div className="skeleton skeleton--row" />
      <div className="skeleton skeleton--row" />
      <div className="skeleton skeleton--line" />
    </div>
  )
}
