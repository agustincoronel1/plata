import AuthGate from './auth/AuthGate'
import AuthProvider from './auth/AuthProvider'
import BackendStatusProvider from './backend/BackendStatusProvider'

/**
 * El sondeo del backend arranca acá, por fuera de la sesión y antes que ella: si Render
 * está dormido, empieza a despertarlo mientras la persona todavía está escribiendo el mail
 * y la contraseña. La pantalla de acceso no depende del backend —el login es contra
 * Supabase—, así que no se bloquea; lo que sí espera a `/health` es el dashboard
 * (ver AuthGate).
 */
export default function App() {
  return (
    <BackendStatusProvider>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </BackendStatusProvider>
  )
}
