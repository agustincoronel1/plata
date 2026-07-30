import { createContext, useContext } from 'react'

/**
 * Contexto de autenticación. Vive en su propio módulo (sin componentes) para que el
 * provider se pueda recargar en caliente sin perder el contexto.
 *
 * El valor lo publica AuthProvider: `{ session, user, loading, signUp, signIn, signOut }`.
 */
export const AuthContext = createContext(null)

/**
 * Acceso a la sesión desde cualquier componente. Falla si no hay provider arriba, en vez
 * de devolver "sin sesión" por accidente.
 */
export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) {
    throw new Error('useAuth necesita estar dentro de <AuthProvider>.')
  }
  return value
}
