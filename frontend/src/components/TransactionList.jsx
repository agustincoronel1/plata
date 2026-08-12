import EmptyState from './EmptyState'
import Icon from './Icon'
import { categoryLabel } from '../services/categories'
import { formatDate, formatMoney } from '../services/format'

/**
 * Lista de movimientos recientes. Ingreso y gasto se distinguen con texto ("Ingreso" /
 * "Gasto"), el signo del monto (+ / −) y el icono: nunca solo por color.
 *
 * Sin movimientos muestra un estado vacío con las dos formas de cargar el primero. Los
 * botones reutilizan los mismos handlers que las acciones del dashboard: acá no se crea
 * ningún formulario nuevo.
 */
export default function TransactionList({
  transactions,
  onEdit,
  onDelete,
  onCreateManual,
  onCreateWithAI,
}) {
  if (transactions.length === 0) {
    return (
      <EmptyState
        icon="receipt"
        title="Todavía no hay movimientos"
        description="Registrá tu primer ingreso o gasto para que Vector calcule cuánto podés usar de verdad."
      >
        <button type="button" className="btn btn--primary" onClick={onCreateManual}>
          Registrar manualmente
        </button>
        <button type="button" className="btn btn--ghost" onClick={onCreateWithAI}>
          Escribilo con IA
        </button>
      </EmptyState>
    )
  }

  return (
    <ul className="row-list">
      {transactions.map((tx) => {
        const isIncome = tx.type === 'income'
        const detail = tx.description || tx.payment_method
        return (
          <li className={`row row--${isIncome ? 'income' : 'expense'}`} key={tx.id}>
            <span className="row__icon">
              <Icon name={isIncome ? 'arrowUp' : 'arrowDown'} />
            </span>

            <div className="row__body">
              <p className="row__title">{categoryLabel(tx.category)}</p>
              <p className="row__meta">
                {isIncome ? 'Ingreso' : 'Gasto'} · {formatDate(tx.occurred_on)}
                {detail && ` · ${detail}`}
              </p>
            </div>

            <p className="row__amount">
              <span aria-hidden="true">{isIncome ? '+ ' : '− '}</span>
              <span className="visually-hidden">{isIncome ? 'Ingreso de ' : 'Gasto de '}</span>
              {formatMoney(tx.amount)}
            </p>

            <div className="row__actions">
              <button
                type="button"
                className="icon-btn"
                onClick={() => onEdit(tx)}
                aria-label={`Editar movimiento de ${tx.category}`}
              >
                <Icon name="pencil" />
              </button>
              <button
                type="button"
                className="icon-btn icon-btn--danger"
                onClick={() => onDelete(tx)}
                aria-label={`Eliminar movimiento de ${tx.category}`}
              >
                <Icon name="trash" />
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
