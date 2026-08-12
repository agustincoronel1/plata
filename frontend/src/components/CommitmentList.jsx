import EmptyState from './EmptyState'
import Icon from './Icon'
import { categoryLabel } from '../services/categories'
import { formatDate, formatMoney } from '../services/format'

const STATUS_LABELS = {
  pending: 'Pendiente',
  paid: 'Pagado',
  cancelled: 'Cancelado',
}

const STATUS_ICONS = {
  pending: 'calendar',
  paid: 'check',
  cancelled: 'close',
}

/**
 * Lista de compromisos. El estado se muestra con texto (Pendiente / Pagado / Cancelado)
 * además del estilo, y las acciones disponibles dependen de ese estado.
 *
 * Marcar como pagado o cancelado es solo un cambio de estado: no crea un movimiento ni
 * modifica el saldo.
 *
 * Sin compromisos la sección no se oculta: son parte del cálculo del disponible, así que
 * el estado vacío explica para qué sirven.
 */
export default function CommitmentList({
  commitments,
  onEdit,
  onDelete,
  onChangeStatus,
  busyId,
  onCreate,
}) {
  if (commitments.length === 0) {
    return (
      <EmptyState
        icon="calendar"
        title="Todavía no hay compromisos"
        description="Son los pagos que ya sabés que tenés que hacer: alquiler, tarjeta, servicios. Vector los descuenta de tu disponible para no prometerte plata que ya está gastada."
      >
        <button type="button" className="btn btn--primary" onClick={onCreate}>
          Agregar tu primer compromiso
        </button>
      </EmptyState>
    )
  }

  // El primero que sigue pendiente es el que se viene: se destaca sobre el resto.
  const nextId = commitments.find((cm) => cm.status === 'pending')?.id

  return (
    <ul className="row-list">
      {commitments.map((cm) => {
        const busy = busyId === cm.id
        const isNext = cm.id === nextId
        return (
          <li className={`cm-item cm-item--${cm.status}`} key={cm.id}>
            <div className={`row row--${cm.status}${isNext ? ' row--next' : ''}`}>
              <span className="row__icon">
                <Icon name={STATUS_ICONS[cm.status] ?? 'calendar'} />
              </span>

              <div className="row__body">
                <p className="row__title">{cm.name}</p>
                <p className="row__meta">
                  Vence {formatDate(cm.due_date)} · {categoryLabel(cm.category)}
                </p>
                <div className="row__tags">
                  <span className={`tag tag--${cm.status}`}>{STATUS_LABELS[cm.status]}</span>
                  {cm.is_recurring && <span className="tag tag--muted">Recurrente</span>}
                </div>
              </div>

              <p className="row__amount">{formatMoney(cm.amount)}</p>

              <div className="row__actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => onEdit(cm)}
                  disabled={busy}
                  aria-label={`Editar ${cm.name}`}
                >
                  <Icon name="pencil" />
                </button>
                <button
                  type="button"
                  className="icon-btn icon-btn--danger"
                  onClick={() => onDelete(cm)}
                  disabled={busy}
                  aria-label={`Eliminar ${cm.name}`}
                >
                  <Icon name="trash" />
                </button>
              </div>
            </div>

            <div className="cm-actions">
              {cm.status === 'pending' ? (
                <>
                  <button
                    type="button"
                    className="btn btn--small btn--secondary"
                    onClick={() => onChangeStatus(cm, 'paid')}
                    disabled={busy}
                    aria-label={`Marcar ${cm.name} como pagado`}
                  >
                    Marcar pagado
                  </button>
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => onChangeStatus(cm, 'cancelled')}
                    disabled={busy}
                    aria-label={`Cancelar ${cm.name}`}
                  >
                    Cancelar
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn btn--small btn--ghost"
                  onClick={() => onChangeStatus(cm, 'pending')}
                  disabled={busy}
                  aria-label={`Volver ${cm.name} a pendiente`}
                >
                  Volver a pendiente
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
