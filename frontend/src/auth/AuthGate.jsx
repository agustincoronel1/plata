import BackendGate from '../backend/BackendGate'
import ApiStatus from '../components/ApiStatus'
import BrandMark from '../components/BrandMark'
import LoadingSkeleton from '../components/LoadingSkeleton'
import DashboardPage from '../pages/DashboardPage'
import AuthScreen from './AuthScreen'
import { useAuth } from './AuthContext'

/**
 * Decide qué se ve: pantalla de acceso o aplicación.
 *
 * Mientras se restaura la sesión guardada no se muestra ninguna de las dos, sino el mismo
 * esqueleto de carga que usa el resto de Vector. Sin ese paso intermedio, al recargar se
 * vería un destello del login antes de entrar (o al revés), que es justo la sensación de
 * "se me cerró la sesión" que no queremos dar.
 *
 * La pantalla de acceso NO espera al backend: iniciar sesión es contra Supabase y funciona
 * aunque Render todavía esté arrancando. El dashboard sí, porque lo primero que hace es
 * pedir cinco cosas a la API; de eso se ocupa `BackendGate`.
 */
export default function AuthGate() {
  const { loading, session, signOut } = useAuth()

  if (loading) {
    return (
      <div className="boot">
        <header className="boot__top">
          <BrandMark />
          <ApiStatus />
        </header>

        <main className="boot__main">
          <LoadingSkeleton />
          <p className="loading-status" role="status" aria-live="polite">
            Recuperando tu sesión…
          </p>
        </main>
      </div>
    )
  }

  if (!session) {
    return <AuthScreen />
  }

  // El dashboard solo existe con sesión y con backend disponible. `onSignOut` es lo único
  // que la aplicación financiera sabe de la autenticación; también se le pasa al gate para
  // poder salir mientras el servidor arranca.
  return (
    <BackendGate onSignOut={signOut}>
      <DashboardPage onSignOut={signOut} />
    </BackendGate>
  )
}
