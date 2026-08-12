import { createClient } from '@supabase/supabase-js'

/**
 * Cliente único de Supabase. Un segundo cliente duplicaría el listener de
 * `onAuthStateChange` y la lógica de refresco, y las dos copias podrían desincronizarse.
 *
 * Solo se usa la publishable key, que está pensada para vivir en el navegador. La secret
 * key y la service_role no entran al frontend.
 */

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabasePublishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

if (!supabaseUrl || !supabasePublishableKey) {
  // Mensaje de desarrollo: dice qué falta y dónde ponerlo, sin revelar ningún valor.
  throw new Error(
    'Faltan las variables de Supabase. Definí VITE_SUPABASE_URL y ' +
      'VITE_SUPABASE_PUBLISHABLE_KEY en frontend/.env.local (mirá frontend/.env.example).',
  )
}

export const supabase = createClient(supabaseUrl, supabasePublishableKey, {
  auth: {
    // La sesión sobrevive a recargar la pestaña: la guarda y la restaura el propio SDK.
    // Vector no escribe ni lee tokens a mano en localStorage.
    persistSession: true,
    // El access token dura poco; el SDK lo renueva solo con el refresh token.
    autoRefreshToken: true,
    // Todavía no hay OAuth ni magic links, así que no hay tokens que leer de la URL.
    detectSessionInUrl: false,
  },
})
