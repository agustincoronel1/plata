import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { supabase } from '../lib/supabase'
import { resetAIUsage } from '../services/aiUsage'
import {
  setAccessTokenProvider,
  setUnauthorizedHandler,
} from '../services/authToken'
import { AuthContext } from './AuthContext'
import { translateAuthError } from './authErrors'

/**
 * Única fuente de verdad de la sesión.
 *
 * Responsabilidades:
 *
 * - Restaurar la sesión guardada al abrir la aplicación (`loading` mientras tanto).
 * - Mantenerla al día con un solo `onAuthStateChange`, que se desuscribe al desmontar.
 * - Exponer signUp / signIn / signOut con errores ya traducidos.
 * - Conectar el token al cliente HTTP, para que ningún componente toque el header.
 *
 * No guarda contraseñas ni tokens: la persistencia es del SDK de Supabase.
 */
export default function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)

  // Espejo de la sesión para leerla fuera de React (el manejador del 401), sin volver a
  // registrar el manejador en cada cambio de sesión.
  const sessionRef = useRef(null)
  sessionRef.current = session

  useEffect(() => {
    // Evita tocar el estado si el componente se desmontó antes de que resuelva la promesa
    // (pasa en StrictMode, que monta, desmonta y vuelve a montar).
    let active = true

    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return
        setSession(data?.session ?? null)
      })
      .catch(() => {
        // Sesión ilegible o corrupta: se arranca deslogueado, que es el estado seguro.
        if (!active) return
        setSession(null)
      })
      .finally(() => {
        if (!active) return
        setLoading(false)
      })

    // Un único listener: login, logout, refresco del token y cambios en otra pestaña.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return
      setSession(nextSession ?? null)
      // Si el evento llega antes que getSession(), ya sabemos qué mostrar.
      setLoading(false)
    })

    return () => {
      active = false
      subscription?.subscription?.unsubscribe()
    }
  }, [])

  useEffect(() => {
    // El cliente HTTP pregunta el token en cada petición; acá se le dice cómo obtenerlo.
    // Se lee la sesión vigente (el SDK la renueva solo), no una copia congelada.
    setAccessTokenProvider(async () => {
      const { data } = await supabase.auth.getSession()
      return data?.session?.access_token ?? null
    })

    // Un 401 con sesión activa significa que el backend ya no la reconoce: se cierra acá
    // y la aplicación vuelve al login. Con la sesión ya cerrada no se hace nada, así no
    // hay una cadena de signOut por cada petición fallida.
    setUnauthorizedHandler(() => {
      if (!sessionRef.current) return
      supabase.auth.signOut().catch(() => {})
      setSession(null)
    })

    return () => {
      setAccessTokenProvider(null)
      setUnauthorizedHandler(null)
    }
  }, [])

  const signUp = useCallback(async (email, password) => {
    const { data, error } = await supabase.auth.signUp({ email, password })
    if (error) {
      throw new Error(translateAuthError(error))
    }

    // El proyecto está configurado para devolver sesión al registrarse. Si algún día deja
    // de hacerlo, se dice claramente en lugar de dejar una pantalla en blanco.
    if (!data?.session) {
      throw new Error('Creamos tu cuenta, pero no quedó iniciada la sesión. Iniciá sesión.')
    }

    setSession(data.session)
    return data.session
  }, [])

  const signIn = useCallback(async (email, password) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) {
      throw new Error(translateAuthError(error))
    }

    setSession(data?.session ?? null)
    return data?.session ?? null
  }, [])

  const signOut = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    // Pase lo que pase del lado del servidor, en este dispositivo la sesión se termina.
    setSession(null)
    // La cuota de IA es de la cuenta que se va: quien entre después no debe ver su saldo
    // de usos. El backend igual la resuelve por token, esto es solo lo que se muestra.
    resetAIUsage()
    if (error) {
      throw new Error(translateAuthError(error))
    }
  }, [])

  const value = useMemo(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      signUp,
      signIn,
      signOut,
    }),
    [session, loading, signUp, signIn, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
