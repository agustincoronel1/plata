import { useState } from 'react'

import ApiStatus from '../components/ApiStatus'
import BrandMark from '../components/BrandMark'
import FeedbackMessage from '../components/FeedbackMessage'
import FormField from '../components/FormField'
import Icon from '../components/Icon'
import { useAuth } from './AuthContext'

export const MIN_PASSWORD_LENGTH = 8

const POINTS = [
  { icon: 'wallet', text: 'Tus datos quedan asociados a tu cuenta, no al navegador.' },
  { icon: 'shield', text: 'La contraseña la maneja Supabase: Vector nunca la guarda.' },
]

/**
 * Pantalla de acceso: iniciar sesión o crear cuenta, en un solo lugar.
 *
 * Es lo primero que se ve sin sesión, así que sostiene la identidad visual del producto
 * (misma marca, mismo layout de arranque, mismos controles) en vez de parecer un formulario
 * pegado aparte.
 *
 * La validación de acá es solo la de forma —correo presente, contraseña de largo mínimo,
 * confirmación que coincide— para no hacer viajes de ida y vuelta por errores obvios. La
 * verdad sobre credenciales, cuentas repetidas y políticas la sigue teniendo Supabase.
 */
export default function AuthScreen() {
  const { signIn, signUp } = useAuth()

  const [mode, setMode] = useState('signIn')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [generalError, setGeneralError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isSignUp = mode === 'signUp'

  function switchTo(nextMode) {
    if (nextMode === mode) return
    setMode(nextMode)
    // Los errores del modo anterior no aplican al nuevo. La contraseña sí se conserva:
    // alternar sin querer no debería obligar a escribirla otra vez.
    setFieldErrors({})
    setGeneralError(null)
    setPasswordConfirm('')
  }

  function validate() {
    const errors = {}

    if (!email.trim()) {
      errors.email = 'Escribí tu correo.'
    }

    if (!password) {
      errors.password = 'Escribí tu contraseña.'
    } else if (isSignUp && password.length < MIN_PASSWORD_LENGTH) {
      errors.password = `La contraseña necesita al menos ${MIN_PASSWORD_LENGTH} caracteres.`
    }

    if (isSignUp && !errors.password && password !== passwordConfirm) {
      errors.passwordConfirm = 'Las contraseñas no coinciden.'
    }

    return errors
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setGeneralError(null)

    const errors = validate()
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      return
    }

    setBusy(true)
    try {
      if (isSignUp) {
        await signUp(email.trim(), password)
      } else {
        await signIn(email.trim(), password)
      }
      // Con sesión iniciada, App cambia de pantalla sola: acá no hay que navegar a nada.
    } catch (error) {
      setGeneralError(error?.message ?? 'No pudimos completar la operación. Intentá de nuevo.')
      setBusy(false)
    }
  }

  return (
    <div className="boot">
      <header className="boot__top">
        <BrandMark />
        <ApiStatus />
      </header>

      <main className="boot__main">
        <section className="welcome auth" aria-labelledby="auth-title">
          <BrandMark size="lg" />

          <h1 className="welcome__title" id="auth-title">
            {isSignUp ? 'Creá tu cuenta.' : 'Entrá a tu plata.'}
          </h1>
          <p className="welcome__lead">
            {isSignUp
              ? 'Con tu cuenta, Vector guarda tu situación financiera y la recupera cuando vuelvas.'
              : 'Tus finanzas, en la dirección correcta.'}
          </p>

          <div className="auth__switch" role="group" aria-label="Iniciar sesión o crear cuenta">
            <button
              type="button"
              className="auth__switch-option"
              aria-pressed={!isSignUp}
              onClick={() => switchTo('signIn')}
            >
              Iniciar sesión
            </button>
            <button
              type="button"
              className="auth__switch-option"
              aria-pressed={isSignUp}
              onClick={() => switchTo('signUp')}
            >
              Crear cuenta
            </button>
          </div>

          <form className="form" onSubmit={handleSubmit} noValidate>
            <FormField id="auth-email" label="Correo electrónico" error={fieldErrors.email}>
              {({ id, describedBy, invalid }) => (
                <input
                  id={id}
                  className="input"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  required
                />
              )}
            </FormField>

            <FormField
              id="auth-password"
              label="Contraseña"
              hint={isSignUp ? `Al menos ${MIN_PASSWORD_LENGTH} caracteres.` : undefined}
              error={fieldErrors.password}
            >
              {({ id, describedBy, invalid }) => (
                <input
                  id={id}
                  className="input"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete={isSignUp ? 'new-password' : 'current-password'}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  required
                />
              )}
            </FormField>

            {isSignUp && (
              <FormField
                id="auth-password-confirm"
                label="Repetí la contraseña"
                error={fieldErrors.passwordConfirm}
              >
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="input"
                    type="password"
                    value={passwordConfirm}
                    onChange={(event) => setPasswordConfirm(event.target.value)}
                    autoComplete="new-password"
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    required
                  />
                )}
              </FormField>
            )}

            <FeedbackMessage feedback={generalError ? { type: 'error', text: generalError } : null} />

            {/* El texto no repite el del selector de arriba: son dos controles distintos
                y con el mismo nombre serían indistinguibles para un lector de pantalla. */}
            <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
              {busy ? 'Un momento…' : isSignUp ? 'Crear mi cuenta' : 'Entrar a mi cuenta'}
            </button>
          </form>

          <ul className="welcome__points">
            {POINTS.map((point) => (
              <li className="welcome__point" key={point.text}>
                <span className="welcome__point-icon">
                  <Icon name={point.icon} />
                </span>
                {point.text}
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="app-footer">
        <p className="app-footer__disclaimer">
          Vector es una herramienta de organización y simulación. No constituye asesoramiento
          financiero.
        </p>
      </footer>
    </div>
  )
}
