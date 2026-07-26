import { formatDate, formatMoney } from '../services/format'

const STATUS_LABELS = {
  pending: 'Pendiente',
  paid: 'Pagado',
  cancelled: 'Cancelado',
}

/**
 * Lista de compromisos. El estado se muestra con texto (Pendiente / Pagado / Cancelado)
 * además del estilo, y las acciones disponibles dependen de ese estado.
 *
 * Marcar como pagado o cancelado es solo un cambio de estado: no crea un movimiento ni
 * modifica el saldo (política del Día 2).
 */
export default function CommitmentList({ commitments, onEdit, onDelete, onChangeStatus, busyId }) {
  if (commitments.length === 0) {
    return (
      <p className="empty">
        No tenés compromisos cargados. Usá “Agregar compromiso” para registrar uno.
      </p>
    )
  }

  return (
    <ul className="cm-list">
      {commitments.map((cm) => {
        const busy = busyId === cm.id
        return (
          <li className={`cm cm--${cm.status}`} key={cm.id}>
            <div className="cm__body">
              <p className="cm__top">
                <span className="cm__name">{cm.name}</span>
                <span className={`tag tag--${cm.status}`}>{STATUS_LABELS[cm.status]}</span>
                {cm.is_recurring && <span className="tag tag--muted">Recurrente</span>}
              </p>
              <p className="cm__meta">
                <span className="cm__amount">{formatMoney(cm.amount)}</span>
                <span> · vence {formatDate(cm.due_date)}</span>
                <span> · {cm.category}</span>
              </p>
            </div>

            <div className="cm__actions">
              {cm.status === 'pending' ? (
                <>
                  <button
                    type="button"
                    className="btn btn--small btn--primary"
                    onClick={() => onChangeStatus(cm, 'paid')}
                    disabled={busy}
                    aria-label={`Marcar ${cm.name} como pagado`}
                  >
                    Marcar pagado
                  </button>
                  <button
                    type="button"
                    className="btn btn--small"
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
                  className="btn btn--small"
                  onClick={() => onChangeStatus(cm, 'pending')}
                  disabled={busy}
                  aria-label={`Volver ${cm.name} a pendiente`}
                >
                  Volver a pendiente
                </button>
              )}
              <button
                type="button"
                className="btn btn--small"
                onClick={() => onEdit(cm)}
                disabled={busy}
                aria-label={`Editar ${cm.name}`}
              >
                Editar
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger-ghost"
                onClick={() => onDelete(cm)}
                disabled={busy}
                aria-label={`Eliminar ${cm.name}`}
              >
                Eliminar
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
