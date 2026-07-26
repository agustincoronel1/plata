import { useCallback, useEffect, useState } from 'react'

import AITransactionDialog from '../components/AITransactionDialog'
import ApiStatus from '../components/ApiStatus'
import CommitmentForm from '../components/CommitmentForm'
import CopilotPanel from '../components/CopilotPanel'
import CommitmentList from '../components/CommitmentList'
import ConfirmDialog from '../components/ConfirmDialog'
import FeedbackMessage from '../components/FeedbackMessage'
import FinancialBreakdown from '../components/FinancialBreakdown'
import MetricCard from '../components/MetricCard'
import ProfileForm from '../components/ProfileForm'
import SimulationForm from '../components/SimulationForm'
import SimulationHistory from '../components/SimulationHistory'
import SimulationResult from '../components/SimulationResult'
import SpendableHeadline from '../components/SpendableHeadline'
import TransactionForm from '../components/TransactionForm'
import TransactionList from '../components/TransactionList'
import {
  ApiError,
  createCommitment,
  createPurchaseSimulation,
  createTransaction,
  deleteCommitment,
  deleteTransaction,
  getCommitments,
  getDashboardSummary,
  getProfile,
  getSimulations,
  getTransactions,
  updateCommitment,
  updateProfile,
  updateTransaction,
} from '../services/api'
import { daysUntilLabel, formatMoney } from '../services/format'
import '../styles/dashboard.css'

function errorFeedback(error, fallback) {
  const text = error instanceof ApiError ? error.message : fallback
  return { type: 'error', text }
}

export default function DashboardPage() {
  const [status, setStatus] = useState('loading')
  const [profile, setProfile] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [commitments, setCommitments] = useState([])
  const [summary, setSummary] = useState(null)
  const [simulations, setSimulations] = useState([])
  const [simulationResult, setSimulationResult] = useState(null)
  const [feedback, setFeedback] = useState(null)

  // Un solo modal por vez: 'profile' | 'transaction' | 'commitment' | 'simulation' | null.
  const [modal, setModal] = useState(null)
  const [editingTransaction, setEditingTransaction] = useState(null)
  const [editingCommitment, setEditingCommitment] = useState(null)
  const [manualPrefill, setManualPrefill] = useState(null)

  const [confirm, setConfirm] = useState(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState(null)
  const [commitmentBusyId, setCommitmentBusyId] = useState(null)

  const load = useCallback(async () => {
    setStatus('loading')
    const [profileResult, txResult, cmResult, summaryResult, simResult] =
      await Promise.allSettled([
        getProfile(),
        getTransactions(),
        getCommitments(),
        getDashboardSummary(),
        getSimulations(),
      ])

    if (profileResult.status === 'fulfilled') {
      setProfile(profileResult.value)
      setTransactions(txResult.status === 'fulfilled' ? txResult.value : [])
      setCommitments(cmResult.status === 'fulfilled' ? cmResult.value : [])
      setSummary(summaryResult.status === 'fulfilled' ? summaryResult.value : null)
      setSimulations(simResult.status === 'fulfilled' ? simResult.value : [])
      setStatus('ready')
      return
    }

    const error = profileResult.reason
    if (error instanceof ApiError && error.status === 404) {
      setStatus('setup')
    } else if (error instanceof ApiError && error.isOffline) {
      setStatus('offline')
    } else {
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const closeModal = useCallback(() => {
    setModal(null)
    setEditingTransaction(null)
    setEditingCommitment(null)
    setManualPrefill(null)
  }, [])

  // El resumen (motor financiero) depende del saldo y de los compromisos: se refresca
  // después de cualquier cambio que los afecte. Es best-effort: si falla, no rompe la UI.
  async function refreshSummary() {
    try {
      setSummary(await getDashboardSummary())
    } catch {
      // Un fallo del resumen no debe tumbar el dashboard; se conserva el anterior.
    }
  }

  async function refreshTransactionsAndBalance() {
    const [profileResult, txResult] = await Promise.allSettled([getProfile(), getTransactions()])
    if (profileResult.status === 'fulfilled') setProfile(profileResult.value)
    if (txResult.status === 'fulfilled') setTransactions(txResult.value)
    await refreshSummary()
  }

  async function refreshCommitments() {
    setCommitments(await getCommitments())
    await refreshSummary()
  }

  // ---------- Perfil ----------

  async function handleProfileSubmit(payload) {
    const saved = await updateProfile(payload)
    setProfile(saved)
    const [txResult, cmResult, simResult] = await Promise.allSettled([
      getTransactions(),
      getCommitments(),
      getSimulations(),
    ])
    setTransactions(txResult.status === 'fulfilled' ? txResult.value : [])
    setCommitments(cmResult.status === 'fulfilled' ? cmResult.value : [])
    setSimulations(simResult.status === 'fulfilled' ? simResult.value : [])
    await refreshSummary()
    setStatus('ready')
    closeModal()
    setFeedback({ type: 'success', text: 'Situación financiera actualizada.' })
  }

  // ---------- Movimientos ----------

  async function handleTransactionSubmit(payload) {
    if (editingTransaction) {
      await updateTransaction(editingTransaction.id, payload)
    } else {
      await createTransaction(payload)
    }
    await refreshTransactionsAndBalance()
    closeModal()
    setFeedback({
      type: 'success',
      text: editingTransaction ? 'Movimiento actualizado.' : 'Movimiento registrado.',
    })
  }

  // ---------- Compromisos ----------

  async function handleCommitmentSubmit(payload) {
    if (editingCommitment) {
      await updateCommitment(editingCommitment.id, payload)
    } else {
      await createCommitment(payload)
    }
    await refreshCommitments()
    closeModal()
    setFeedback({
      type: 'success',
      text: editingCommitment ? 'Compromiso actualizado.' : 'Compromiso agregado.',
    })
  }

  async function handleChangeStatus(commitment, nextStatus) {
    setCommitmentBusyId(commitment.id)
    setFeedback(null)
    try {
      await updateCommitment(commitment.id, { status: nextStatus })
      await refreshCommitments()
      const labels = { paid: 'pagado', cancelled: 'cancelado', pending: 'pendiente' }
      setFeedback({ type: 'success', text: `Compromiso marcado como ${labels[nextStatus]}.` })
    } catch (error) {
      setFeedback(errorFeedback(error, 'No se pudo actualizar el compromiso.'))
    } finally {
      setCommitmentBusyId(null)
    }
  }

  // ---------- Simulación de compra ----------

  async function handleSimulationSubmit(payload) {
    const created = await createPurchaseSimulation(payload)
    setSimulationResult(created)
    setSimulations((current) => [created, ...current].slice(0, 10))
    closeModal()
    setFeedback({ type: 'success', text: 'Simulación lista.' })
  }

  // ---------- Borrado (con confirmación) ----------

  function askDeleteTransaction(item) {
    setConfirmError(null)
    setConfirm({ kind: 'transaction', item })
  }

  function askDeleteCommitment(item) {
    setConfirmError(null)
    setConfirm({ kind: 'commitment', item })
  }

  async function handleConfirmDelete() {
    setConfirmBusy(true)
    setConfirmError(null)
    try {
      if (confirm.kind === 'transaction') {
        await deleteTransaction(confirm.item.id)
        await refreshTransactionsAndBalance()
        setFeedback({ type: 'success', text: 'Movimiento eliminado.' })
      } else {
        await deleteCommitment(confirm.item.id)
        await refreshCommitments()
        setFeedback({ type: 'success', text: 'Compromiso eliminado.' })
      }
      setConfirm(null)
    } catch (error) {
      setConfirmError(error instanceof ApiError ? error.message : 'No se pudo eliminar.')
    } finally {
      setConfirmBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1 className="header__brand">Plata</h1>
          <p className="header__tagline">Tu margen real para llegar tranquilo a fin de mes</p>
        </div>
        <ApiStatus />
      </header>

      <main>
        {status === 'loading' && (
          <p className="state" role="status" aria-live="polite">
            Cargando tu situación…
          </p>
        )}

        {status === 'offline' && (
          <section className="state state--warning">
            <h2>No pudimos conectar con el servidor</h2>
            <p>Revisá que el backend esté activo y volvé a intentar.</p>
            <button type="button" className="btn btn--primary" onClick={load}>
              Reintentar
            </button>
          </section>
        )}

        {status === 'error' && (
          <section className="state state--warning">
            <h2>Algo salió mal</h2>
            <p>No pudimos cargar tus datos. Intentá de nuevo en un momento.</p>
            <button type="button" className="btn btn--primary" onClick={load}>
              Reintentar
            </button>
          </section>
        )}

        {status === 'setup' && (
          <section className="state">
            <h2>Configurá Plata para empezar</h2>
            <p>
              Todavía no cargaste tu situación financiera. Ingresá tu saldo y tus datos para
              ver tu dashboard.
            </p>
            <button type="button" className="btn btn--primary" onClick={() => setModal('profile')}>
              Configurar situación
            </button>
          </section>
        )}

        {status === 'ready' && profile && (
          <>
            <FeedbackMessage feedback={feedback} />

            <SpendableHeadline summary={summary} />

            <section aria-labelledby="tu-situacion">
              <div className="section-head">
                <h2 className="section-head__title" id="tu-situacion">
                  Tu situación
                </h2>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => setModal('profile')}
                >
                  Editar situación
                </button>
              </div>
              <ul className="metrics">
                <MetricCard
                  label="Saldo actual"
                  value={formatMoney(profile.current_balance)}
                  hint="El dinero que tenés hoy."
                />
                <MetricCard
                  label="Días hasta cobrar"
                  value={daysUntilLabel(profile.next_income_date)}
                  hint="Tiempo hasta tu próximo ingreso."
                />
                <MetricCard
                  label="Dinero protegido"
                  value={formatMoney(profile.protected_amount)}
                  hint="Reserva que Plata no considera disponible."
                />
              </ul>
              <FinancialBreakdown summary={summary} />
            </section>

            <h2 className="visually-hidden">Acciones</h2>
            <div className="actions">
              <button
                type="button"
                className="action action--primary action--enabled"
                onClick={() => {
                  setEditingTransaction(null)
                  setModal('transaction')
                }}
              >
                Registrar movimiento
              </button>
              <button
                type="button"
                className="action action--secondary action--enabled"
                onClick={() => {
                  setEditingTransaction(null)
                  setManualPrefill(null)
                  setModal('ai-transaction')
                }}
              >
                Escribilo con IA
              </button>
              <button
                type="button"
                className="action action--secondary action--enabled"
                onClick={() => setModal('simulation')}
              >
                Simular compra
              </button>
            </div>

            <CopilotPanel onActionApplied={refreshTransactionsAndBalance} />

            {simulationResult && (
              <section aria-labelledby="sim-result">
                <div className="section-head">
                  <h2 className="section-head__title" id="sim-result">
                    Resultado de la simulación
                  </h2>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => setSimulationResult(null)}
                  >
                    Ocultar
                  </button>
                </div>
                <SimulationResult simulation={simulationResult} />
              </section>
            )}

            <section aria-labelledby="movimientos">
              <div className="section-head">
                <h2 className="section-head__title" id="movimientos">
                  Movimientos recientes
                </h2>
              </div>
              <TransactionList
                transactions={transactions}
                onEdit={(tx) => {
                  setEditingTransaction(tx)
                  setModal('transaction')
                }}
                onDelete={askDeleteTransaction}
              />
            </section>

            <section aria-labelledby="compromisos">
              <div className="section-head">
                <h2 className="section-head__title" id="compromisos">
                  Próximos compromisos
                </h2>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => {
                    setEditingCommitment(null)
                    setModal('commitment')
                  }}
                >
                  Agregar compromiso
                </button>
              </div>
              <CommitmentList
                commitments={commitments}
                onEdit={(cm) => {
                  setEditingCommitment(cm)
                  setModal('commitment')
                }}
                onDelete={askDeleteCommitment}
                onChangeStatus={handleChangeStatus}
                busyId={commitmentBusyId}
              />
            </section>

            <section aria-labelledby="simulaciones">
              <div className="section-head">
                <h2 className="section-head__title" id="simulaciones">
                  Simulaciones recientes
                </h2>
              </div>
              <SimulationHistory simulations={simulations} />
            </section>
          </>
        )}
      </main>

      <footer className="footer">
        <p className="footer__claim">
          No te dice solamente cuánto dinero tenés. <em>Te dice cuánto podés usar.</em>
        </p>
        <p className="footer__disclaimer">
          Plata es una herramienta de organización y simulación. No constituye asesoramiento
          financiero.
        </p>
      </footer>

      {modal === 'profile' && (
        <ProfileForm
          profile={profile}
          isSetup={status === 'setup'}
          onSubmit={handleProfileSubmit}
          onClose={closeModal}
        />
      )}

      {modal === 'transaction' && (
        <TransactionForm
          transaction={editingTransaction}
          prefill={manualPrefill}
          onSubmit={handleTransactionSubmit}
          onClose={closeModal}
        />
      )}

      {modal === 'ai-transaction' && (
        <AITransactionDialog
          onRegistered={async () => {
            await refreshTransactionsAndBalance()
            closeModal()
            setFeedback({ type: 'success', text: 'Movimiento interpretado y registrado.' })
          }}
          onFallback={(text) => {
            setManualPrefill({ description: text })
            setModal('transaction')
          }}
          onClose={closeModal}
        />
      )}

      {modal === 'commitment' && (
        <CommitmentForm
          commitment={editingCommitment}
          onSubmit={handleCommitmentSubmit}
          onClose={closeModal}
        />
      )}

      {modal === 'simulation' && (
        <SimulationForm onSubmit={handleSimulationSubmit} onClose={closeModal} />
      )}

      {confirm && (
        <ConfirmDialog
          title={confirm.kind === 'transaction' ? 'Eliminar movimiento' : 'Eliminar compromiso'}
          message={
            confirm.kind === 'transaction'
              ? '¿Seguro que querés eliminar este movimiento? El saldo se ajustará.'
              : '¿Seguro que querés eliminar este compromiso?'
          }
          busy={confirmBusy}
          error={confirmError}
          onConfirm={handleConfirmDelete}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
