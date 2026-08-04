import { useCallback, useEffect, useState } from 'react'

import { useBackendStatus } from '../backend/BackendStatusContext'
import AITransactionDialog from '../components/AITransactionDialog'
import ApiStatus from '../components/ApiStatus'
import AppShell from '../components/AppShell'
import BalanceHero from '../components/BalanceHero'
import BrandMark from '../components/BrandMark'
import CategoryChart from '../components/CategoryChart'
import CommitmentForm from '../components/CommitmentForm'
import CopilotDock from '../components/CopilotDock'
import CopilotPanel from '../components/CopilotPanel'
import CommitmentList from '../components/CommitmentList'
import ConfirmDialog from '../components/ConfirmDialog'
import FeedbackMessage from '../components/FeedbackMessage'
import FinancialBreakdown from '../components/FinancialBreakdown'
import Icon from '../components/Icon'
import LoadingSkeleton from '../components/LoadingSkeleton'
import MetricCard from '../components/MetricCard'
import ProfileForm from '../components/ProfileForm'
import QuickActions from '../components/QuickActions'
import SectionCard from '../components/SectionCard'
import SimulationForm from '../components/SimulationForm'
import SimulationHistory from '../components/SimulationHistory'
import SimulationResult from '../components/SimulationResult'
import TransactionForm from '../components/TransactionForm'
import TransactionList from '../components/TransactionList'
import WelcomeScreen from '../components/WelcomeScreen'
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
import { buildMonthInsight } from '../services/insights'
import '../styles/dashboard.css'

function errorFeedback(error, fallback) {
  const text = error instanceof ApiError ? error.message : fallback
  return { type: 'error', text }
}

const FOOTER = (
  <>
    <p className="app-footer__claim">
      No te dice solamente cuánto dinero tenés. <em>Te dice cuánto podés usar.</em>
    </p>
    <p className="app-footer__disclaimer">
      Plata es una herramienta de organización y simulación. No constituye asesoramiento
      financiero.
    </p>
  </>
)

/**
 * `onSignOut` es lo único que esta pantalla sabe de la autenticación: llega desde arriba
 * y solo sirve para dibujar el control de cierre de sesión. Si no se pasa (por ejemplo, al
 * montar el dashboard aislado en un test), no se dibuja y el resto funciona igual.
 */
export default function DashboardPage({ onSignOut }) {
  // Si el backend deja de responder mientras la aplicación está abierta, se le avisa a
  // quien lleva la cuenta de la disponibilidad en vez de resolverlo acá: el ciclo de
  // reintentos y el mensaje de arranque viven en un solo lugar.
  const { reportUnavailable } = useBackendStatus()
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

  // Navegación: una sola pantalla con secciones, sin router.
  const [activeSection, setActiveSection] = useState('inicio')
  const [copilotOpen, setCopilotOpen] = useState(false)

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
    } else if (error instanceof ApiError && (error.isOffline || error.timeout)) {
      // Sin conexión o petición vencida: puede ser que Render se haya vuelto a dormir.
      // Un 401 (sesión), un 429 (límite diario) o un 500 (error real) NO pasan por acá.
      setStatus('offline')
      reportUnavailable()
    } else {
      setStatus('error')
    }
  }, [reportUnavailable])

  useEffect(() => {
    load()
  }, [load])

  const closeModal = useCallback(() => {
    setModal(null)
    setEditingTransaction(null)
    setEditingCommitment(null)
    setManualPrefill(null)
  }, [])

  // La navegación no cambia de ruta: marca la sección activa y la trae a la vista.
  function goToSection(id) {
    setActiveSection(id)
    if (id === 'copiloto') setCopilotOpen(true)
    const target = document.getElementById(id)
    if (target && typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  // Aperturas de formularios. Las acciones rápidas y los estados vacíos de cada sección
  // llaman exactamente a estas funciones: mismos formularios, mismo flujo de confirmación.
  function openNewTransaction(type = null) {
    setEditingTransaction(null)
    // Solo preselecciona el tipo cuando se pide explícitamente (no cuando llega un evento).
    setManualPrefill(type === 'income' || type === 'expense' ? { type } : null)
    setModal('transaction')
  }

  function openAITransaction() {
    setEditingTransaction(null)
    setManualPrefill(null)
    setModal('ai-transaction')
  }

  function openNewCommitment() {
    setEditingCommitment(null)
    setModal('commitment')
  }

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
      if (nextStatus === 'paid' || commitment.status === 'paid') {
        const [profileResult, txResult, cmResult, summaryResult] = await Promise.allSettled([
          getProfile(),
          getTransactions(),
          getCommitments(),
          getDashboardSummary(),
        ])
        if (profileResult.status === 'fulfilled') setProfile(profileResult.value)
        if (txResult.status === 'fulfilled') setTransactions(txResult.value)
        if (cmResult.status === 'fulfilled') setCommitments(cmResult.value)
        if (summaryResult.status === 'fulfilled') setSummary(summaryResult.value)
      } else {
        await refreshCommitments()
      }
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

  // Conclusión breve del mes (gastos vs. mes anterior + categoría principal). Es una
  // lectura de datos ya calculados: no hay cuentas nuevas ni IA.
  const monthInsight = buildMonthInsight(summary)

  // Los diálogos son los mismos en cualquier estado de la pantalla: se arman una sola vez.
  const overlays = (
    <>
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
    </>
  )

  // Carga, error de conexión y onboarding: pantalla propia, sin dashboard vacío detrás.
  if (status !== 'ready' || !profile) {
    return (
      <div className="boot">
        <header className="boot__top">
          <BrandMark />
          <div className="boot__actions">
            <ApiStatus />
            {/* También hay que poder salir antes de terminar el onboarding. */}
            {onSignOut && (
              <button
                type="button"
                className="topbar__signout"
                onClick={onSignOut}
                aria-label="Cerrar sesión"
                title="Cerrar sesión"
              >
                <Icon name="logout" />
              </button>
            )}
          </div>
        </header>

        <main className="boot__main">
          {status === 'loading' && (
            <>
              <LoadingSkeleton />
              <p className="loading-status" role="status" aria-live="polite">
                Cargando tu situación…
              </p>
            </>
          )}

          {status === 'offline' && (
            <section className="state state--warning">
              <span className="state__icon">
                <Icon name="alert" />
              </span>
              <h2>No pudimos conectar con el servidor</h2>
              <p>Revisá que el backend esté activo y volvé a intentar.</p>
              <button type="button" className="btn btn--primary" onClick={load}>
                Reintentar
              </button>
            </section>
          )}

          {status === 'error' && (
            <section className="state state--warning">
              <span className="state__icon">
                <Icon name="alert" />
              </span>
              <h2>Algo salió mal</h2>
              <p>No pudimos cargar tus datos. Intentá de nuevo en un momento.</p>
              <button type="button" className="btn btn--primary" onClick={load}>
                Reintentar
              </button>
            </section>
          )}

          {status === 'setup' && <WelcomeScreen onStart={() => setModal('profile')} />}
        </main>

        <footer className="app-footer">{FOOTER}</footer>

        {overlays}
      </div>
    )
  }

  return (
    <AppShell
      userName={profile.name}
      active={activeSection}
      onNavigate={goToSection}
      onOpenProfile={() => setModal('profile')}
      onOpenCopilot={() => goToSection('copiloto')}
      onSignOut={onSignOut}
      copilotOpen={copilotOpen}
      footer={FOOTER}
    >
      <FeedbackMessage feedback={feedback} />

      <div className="dashboard">
        <div className="dashboard__main">
          <BalanceHero summary={summary} onOpenDetail={() => goToSection('situacion')} />

          {/* Ingresos, gastos y ahorro del mes: una fila compacta, con los valores que ya
              vienen calculados en el resumen. */}
          <ul className="metrics metrics--compact" aria-label="Tu mes">
            <MetricCard label="Ingresos" value={formatMoney(summary?.month_income_total)} />
            <MetricCard label="Gastos" value={formatMoney(summary?.month_expenses_total)} />
            <MetricCard label="Ahorro" value={formatMoney(summary?.month_savings)} />
          </ul>

          <QuickActions
            onExpense={() => openNewTransaction('expense')}
            onIncome={() => openNewTransaction('income')}
            onAI={openAITransaction}
            onSimulate={() => setModal('simulation')}
          />

          <SectionCard
            id="categorias"
            title="En qué se fue tu plata"
            titleId="categorias-title"
            subtitle="Tus gastos de este mes, por categoría."
          >
            <CategoryChart items={summary?.category_summary} />
          </SectionCard>

          <SectionCard
            id="situacion"
            title="Tu situación"
            titleId="tu-situacion"
            action={
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => setModal('profile')}
              >
                Editar situación
              </button>
            }
          >
            {/* Conclusión en una línea, con datos reales del resumen. Si no alcanzan, no
                se dice nada en vez de inventar una lectura. */}
            {monthInsight && <p className="situation__insight">{monthInsight}</p>}

            <ul className="metrics metrics--compact">
              <MetricCard
                label="Compromisos"
                value={formatMoney(summary?.pending_commitments_amount)}
              />
              <MetricCard
                label="Dinero protegido"
                value={formatMoney(profile.protected_amount)}
              />
              <MetricCard
                label="Días hasta cobrar"
                value={daysUntilLabel(profile.next_income_date)}
              />
            </ul>

            {/* El desglose completo queda a un clic: informa igual, pero no ocupa media
                pantalla en la vista por defecto. */}
            <details className="situation__detail">
              <summary>Ver el detalle del cálculo</summary>
              <FinancialBreakdown summary={summary} />
            </details>
          </SectionCard>

          <SectionCard
            id="movimientos"
            title="Movimientos recientes"
            titleId="movimientos-title"
            action={
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={() => openNewTransaction()}
              >
                <Icon name="plus" />
                Registrar movimiento
              </button>
            }
          >
            <TransactionList
              transactions={transactions}
              onEdit={(tx) => {
                setEditingTransaction(tx)
                setModal('transaction')
              }}
              onDelete={askDeleteTransaction}
              onCreateManual={() => openNewTransaction()}
              onCreateWithAI={openAITransaction}
            />
          </SectionCard>

          <SectionCard
            id="compromisos"
            title="Próximos compromisos"
            titleId="compromisos-title"
            subtitle="Se descuentan de tu dinero disponible."
            action={
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={openNewCommitment}
              >
                <Icon name="plus" />
                Agregar compromiso
              </button>
            }
          >
            <CommitmentList
              commitments={commitments}
              onEdit={(cm) => {
                setEditingCommitment(cm)
                setModal('commitment')
              }}
              onDelete={askDeleteCommitment}
              onChangeStatus={handleChangeStatus}
              busyId={commitmentBusyId}
              onCreate={openNewCommitment}
            />
          </SectionCard>

          <SectionCard
            id="simulaciones"
            title="Simulador de compras"
            titleId="simulador"
            subtitle="Simular no registra un gasto ni modifica tu saldo."
            action={
              <button
                type="button"
                className="btn btn--primary btn--small"
                onClick={() => setModal('simulation')}
              >
                Nueva simulación
              </button>
            }
          >
            {simulationResult && (
              <section className="sim-block" aria-labelledby="sim-result">
                <div className="card__head">
                  <h3 className="card__title" id="sim-result">
                    Resultado de la simulación
                  </h3>
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

            <div className="sim-block">
              <h3 className="sim-block__title" id="simulaciones-recientes">
                Simulaciones recientes
              </h3>
              <SimulationHistory
                simulations={simulations}
                onSimulate={() => setModal('simulation')}
              />
            </div>
          </SectionCard>
        </div>

        <div className="dashboard__aside">
          <CopilotDock open={copilotOpen} onClose={() => setCopilotOpen(false)}>
            <CopilotPanel onActionApplied={refreshTransactionsAndBalance} />
          </CopilotDock>
        </div>
      </div>

      {overlays}
    </AppShell>
  )
}
