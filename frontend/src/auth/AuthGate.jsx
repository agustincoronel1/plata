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
 * esqueleto de carga que usa el resto de Plata. Sin ese paso intermedio, al recargar se
 * vería un destello del login antes de entrar (o al revés), que es justo la sensación de
 * "se me cerró la sesión" que no queremos dar.
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

  // El dashboard solo existe con sesión. `onSignOut` es lo único que la aplicación
  // financiera sabe de la autenticación.
  return <DashboardPage onSignOut={signOut} />
}
