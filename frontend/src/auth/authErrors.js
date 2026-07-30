/**
 * Traducción de los errores de Supabase Auth a mensajes en español, breves y accionables.
 *
 * El texto crudo del proveedor no se muestra: viene en inglés, cambia entre versiones y a
 * veces describe la implementación. Cuando el caso no está mapeado se muestra un mensaje
 * genérico, nunca el original.
 *
 * Los mensajes de login no distinguen "el correo no existe" de "la contraseña está mal":
 * esa diferencia le sirve más a quien prueba correos ajenos que a quien se equivocó.
 */

const BY_CODE = {
  invalid_credentials: 'Correo o contraseña incorrectos.',
  email_not_confirmed: 'Todavía falta confirmar esta cuenta.',
  user_already_exists: 'Ya existe una cuenta con ese correo. Probá iniciar sesión.',
  email_exists: 'Ya existe una cuenta con ese correo. Probá iniciar sesión.',
  weak_password: 'Elegí una contraseña más segura, de al menos 8 caracteres.',
  validation_failed: 'Revisá el correo y la contraseña.',
  over_request_rate_limit: 'Demasiados intentos. Esperá un momento y probá de nuevo.',
  over_email_send_rate_limit: 'Demasiados intentos. Esperá un momento y probá de nuevo.',
  signup_disabled: 'El registro está deshabilitado por ahora.',
}

// Respaldo para versiones del SDK que solo traen el mensaje en inglés.
const BY_MESSAGE = [
  [/invalid login credentials/i, BY_CODE.invalid_credentials],
  [/email not confirmed/i, BY_CODE.email_not_confirmed],
  [/already registered|already exists/i, BY_CODE.user_already_exists],
  [/password should be/i, BY_CODE.weak_password],
  [/unable to validate email|invalid format/i, 'Revisá el correo: no parece válido.'],
  [/rate limit/i, BY_CODE.over_request_rate_limit],
  [/signups? (not allowed|disabled)/i, BY_CODE.signup_disabled],
  [/failed to fetch|network/i, 'No pudimos conectar. Revisá tu conexión e intentá de nuevo.'],
]

export const GENERIC_AUTH_ERROR = 'No pudimos completar la operación. Intentá de nuevo.'

export function translateAuthError(error) {
  if (!error) {
    return GENERIC_AUTH_ERROR
  }

  const mapped = BY_CODE[error.code]
  if (mapped) {
    return mapped
  }

  const message = typeof error.message === 'string' ? error.message : ''
  for (const [pattern, text] of BY_MESSAGE) {
    if (pattern.test(message)) {
      return text
    }
  }

  return GENERIC_AUTH_ERROR
}
