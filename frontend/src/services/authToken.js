/**
 * Puente entre la sesión de Supabase y el cliente HTTP: la capa de autenticación registra
 * acá cómo obtener el token y `api.js` solo pregunta, sin conocer al SDK.
 *
 * No se guarda ninguna copia del token. El proveedor lee la sesión vigente en cada
 * petición, así que un token renovado se usa de inmediato y uno caducado nunca se
 * reutiliza desde una variable vieja.
 */

let accessTokenProvider = null
let unauthorizedHandler = null

/**
 * Registra la función que devuelve el access token actual (o null si no hay sesión).
 * Pasar `null` la desregistra: a partir de ahí las peticiones salen sin Authorization.
 */
export function setAccessTokenProvider(provider) {
  accessTokenProvider = typeof provider === 'function' ? provider : null
}

/**
 * Token de la sesión vigente, o null si no hay sesión o no se pudo obtener. Nunca lanza:
 * la petición sale sin header y el backend decide con un 401 si lo necesitaba.
 */
export async function getAccessToken() {
  if (!accessTokenProvider) {
    return null
  }

  try {
    const token = await accessTokenProvider()
    return typeof token === 'string' && token ? token : null
  } catch {
    return null
  }
}

/**
 * Registra qué hacer cuando el backend responde 401 con una sesión que el frontend creía
 * válida (token revocado, proyecto rotado, sesión cerrada en otra pestaña).
 */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === 'function' ? handler : null
}

/**
 * Avisa de un 401. Una vez por respuesta y sin reintentar la petición: quien escucha
 * cierra la sesión y la aplicación vuelve al login, sin ciclo infinito.
 */
export function notifyUnauthorized() {
  if (!unauthorizedHandler) {
    return
  }

  try {
    unauthorizedHandler()
  } catch {
    // Un error del manejador no debe tapar el error real de la petición.
  }
}

/** Limpia ambos registros. Pensado para los tests. */
export function resetAuthBridge() {
  accessTokenProvider = null
  unauthorizedHandler = null
}
