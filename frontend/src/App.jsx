import AuthGate from './auth/AuthGate'
import AuthProvider from './auth/AuthProvider'

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  )
}
