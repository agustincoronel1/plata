import Icon from './Icon'

/**
 * Accesos directos debajo del saldo. Cada uno abre exactamente el mismo formulario que ya
 * existía: no hay flujos nuevos ni lógica duplicada.
 *
 * "Escribilo con IA" abre el borrador asistido, que sigue aclarando que nada se registra
 * hasta confirmar.
 */
export default function QuickActions({ onExpense, onIncome, onAI, onSimulate }) {
  const actions = [
    { key: 'expense', label: 'Registrar gasto', icon: 'arrowDown', onClick: onExpense },
    { key: 'income', label: 'Registrar ingreso', icon: 'arrowUp', onClick: onIncome },
    { key: 'ai', label: 'Escribilo con IA', icon: 'sparkle', onClick: onAI, accent: true },
    { key: 'simulate', label: 'Simular compra', icon: 'calculator', onClick: onSimulate },
  ]

  return (
    <section className="quick" aria-label="Acciones rápidas">
      {actions.map((action) => (
        <button
          key={action.key}
          type="button"
          className={`quick-action${action.accent ? ' quick-action--accent' : ''}`}
          onClick={action.onClick}
        >
          <span className="quick-action__icon">
            <Icon name={action.icon} />
          </span>
          {action.label}
        </button>
      ))}
    </section>
  )
}
