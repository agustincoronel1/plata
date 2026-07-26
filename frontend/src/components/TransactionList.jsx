import { formatDate, formatMoney } from '../services/format'

/**
 * Lista de movimientos recientes. Ingreso y gasto se distinguen con texto ("Ingreso" /
 * "Gasto"), el signo del monto (+ / −) y estilo: nunca solo por color.
 */
export default function TransactionList({ transactions, onEdit, onDelete }) {
  if (transactions.length === 0) {
    return (
      <p className="empty">
        Todavía no registraste movimientos. Usá “Registrar movimiento” para cargar el
        primero.
      </p>
    )
  }

  return (
    <ul className="tx-list">
      {transactions.map((tx) => {
        const isIncome = tx.type === 'income'
        const detail = tx.description || tx.payment_method
        return (
          <li className={`tx tx--${isIncome ? 'income' : 'expense'}`} key={tx.id}>
            <div className="tx__body">
              <p className="tx__top">
                <span className={`tag tag--${isIncome ? 'income' : 'expense'}`}>
                  {isIncome ? 'Ingreso' : 'Gasto'}
                </span>
                <span className="tx__category">{tx.category}</span>
              </p>
              <p className="tx__meta">
                <span>{formatDate(tx.occurred_on)}</span>
                {detail && <span className="tx__detail"> · {detail}</span>}
              </p>
            </div>

            <p className="tx__amount">
              <span aria-hidden="true">{isIncome ? '+ ' : '− '}</span>
              <span className="visually-hidden">{isIncome ? 'Ingreso de ' : 'Gasto de '}</span>
              {formatMoney(tx.amount)}
            </p>

            <div className="tx__actions">
              <button
                type="button"
                className="btn btn--small"
                onClick={() => onEdit(tx)}
                aria-label={`Editar movimiento de ${tx.category}`}
              >
                Editar
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger-ghost"
                onClick={() => onDelete(tx)}
                aria-label={`Eliminar movimiento de ${tx.category}`}
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
