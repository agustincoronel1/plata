import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from './AuthContext'
import AuthScreen from './AuthScreen'

/**
 * Pantalla de acceso aislada: el provider real se sustituye por un valor de contexto con
 * espías. Lo que se prueba acá es el formulario —qué se muestra, qué se valida antes de
 * salir a la red y qué se hace con los errores—, no cómo se guarda la sesión.
 */

let auth

function renderScreen(overrides = {}) {
  auth = {
    session: null,
    user: null,
    loading: false,
    signUp: vi.fn(async () => {}),
    signIn: vi.fn(async () => {}),
    signOut: vi.fn(async () => {}),
    ...overrides,
  }

  return render(
    <AuthContext.Provider value={auth}>
      <AuthScreen />
    </AuthContext.Provider>,
  )
}

const email = () => screen.getByLabelText('Correo electrónico')
const password = () => screen.getByLabelText('Contraseña')
const passwordConfirm = () => screen.getByLabelText('Repetí la contraseña')

// El selector de modo y el botón de envío tienen nombres distintos a propósito: dos
// controles con el mismo nombre accesible son indistinguibles para un lector de pantalla.
const tabSignIn = () => screen.getByRole('button', { name: 'Iniciar sesión' })
const tabSignUp = () => screen.getByRole('button', { name: 'Crear cuenta' })
const submitSignIn = () => screen.getByRole('button', { name: 'Entrar a mi cuenta' })
const submitSignUp = () => screen.getByRole('button', { name: 'Crear mi cuenta' })

beforeEach(() => {
  // ApiStatus consulta /health al montar; acá responde que sí, sin salir a la red.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('pantalla de acceso', () => {
  it('sin sesión arranca en iniciar sesión, no en registro', () => {
    renderScreen()

    expect(screen.getByRole('heading', { name: 'Entrá a tu plata.' })).toBeInTheDocument()
    expect(tabSignIn()).toHaveAttribute('aria-pressed', 'true')
    expect(tabSignUp()).toHaveAttribute('aria-pressed', 'false')
    // El campo de confirmación es exclusivo del registro.
    expect(screen.queryByLabelText('Repetí la contraseña')).not.toBeInTheDocument()
  })

  it('conserva la identidad visual de Plata', () => {
    renderScreen()

    expect(screen.getAllByRole('img', { name: 'Plata' }).length).toBeGreaterThan(0)
    expect(screen.getByText(/no constituye asesoramiento financiero/i)).toBeInTheDocument()
  })

  it('permite alternar entre registro e inicio de sesión', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(tabSignUp())

    expect(screen.getByRole('heading', { name: 'Creá tu cuenta.' })).toBeInTheDocument()
    expect(passwordConfirm()).toBeInTheDocument()
    expect(tabSignUp()).toHaveAttribute('aria-pressed', 'true')
    expect(submitSignUp()).toBeInTheDocument()

    await user.click(tabSignIn())

    expect(screen.getByRole('heading', { name: 'Entrá a tu plata.' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Repetí la contraseña')).not.toBeInTheDocument()
    expect(submitSignIn()).toBeInTheDocument()
  })

  it('todavía no ofrece Google, magic links ni recuperar contraseña', () => {
    renderScreen()

    expect(screen.queryByText(/google/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/olvid/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/enlace mágico|magic link/i)).not.toBeInTheDocument()
  })
})

describe('validación antes de llamar a Supabase', () => {
  it('pide el correo cuando está vacío', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.type(password(), 'contrasena-larga')
    await user.click(submitSignIn())

    expect(await screen.findByText('Escribí tu correo.')).toBeInTheDocument()
    expect(auth.signIn).not.toHaveBeenCalled()
  })

  it('pide la contraseña cuando está vacía', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.type(email(), 'persona@ejemplo.test')
    await user.click(submitSignIn())

    expect(await screen.findByText('Escribí tu contraseña.')).toBeInTheDocument()
    expect(auth.signIn).not.toHaveBeenCalled()
  })

  it('rechaza una contraseña de menos de 8 caracteres al registrarse', async () => {
    const user = userEvent.setup()
    renderScreen()
    await user.click(tabSignUp())

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'corta7')
    await user.type(passwordConfirm(), 'corta7')
    await user.click(submitSignUp())

    expect(
      await screen.findByText('La contraseña necesita al menos 8 caracteres.'),
    ).toBeInTheDocument()
    expect(auth.signUp).not.toHaveBeenCalled()
  })

  it('rechaza el registro si las contraseñas no coinciden', async () => {
    const user = userEvent.setup()
    renderScreen()
    await user.click(tabSignUp())

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'contrasena-larga')
    await user.type(passwordConfirm(), 'contrasena-distinta')
    await user.click(submitSignUp())

    expect(await screen.findByText('Las contraseñas no coinciden.')).toBeInTheDocument()
    expect(auth.signUp).not.toHaveBeenCalled()
  })

  it('el error queda asociado al campo con aria-invalid', async () => {
    const user = userEvent.setup()
    renderScreen()
    await user.click(tabSignUp())

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'contrasena-larga')
    await user.type(passwordConfirm(), 'otra-cosa')
    await user.click(submitSignUp())

    await screen.findByText('Las contraseñas no coinciden.')
    expect(passwordConfirm()).toHaveAttribute('aria-invalid', 'true')
    expect(password()).toHaveAttribute('aria-invalid', 'false')
  })

  it('en el login no se exige el largo mínimo: lo resuelve el servidor', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'vieja')
    await user.click(submitSignIn())

    expect(auth.signIn).toHaveBeenCalledWith('persona@ejemplo.test', 'vieja')
  })
})

describe('envío', () => {
  it('un registro válido llama a signUp con el correo y la contraseña', async () => {
    const user = userEvent.setup()
    renderScreen()
    await user.click(tabSignUp())

    await user.type(email(), '  nueva@ejemplo.test  ')
    await user.type(password(), 'contrasena-larga')
    await user.type(passwordConfirm(), 'contrasena-larga')
    await user.click(submitSignUp())

    // El correo va sin espacios de más; la contraseña, tal cual se escribió.
    expect(auth.signUp).toHaveBeenCalledWith('nueva@ejemplo.test', 'contrasena-larga')
  })

  it('un login válido llama a signIn', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'contrasena-larga')
    await user.click(submitSignIn())

    expect(auth.signIn).toHaveBeenCalledWith('persona@ejemplo.test', 'contrasena-larga')
  })

  it('muestra el error del registro sin perder lo escrito', async () => {
    const user = userEvent.setup()
    renderScreen({
      signUp: vi.fn(async () => {
        throw new Error('Ya existe una cuenta con ese correo. Probá iniciar sesión.')
      }),
    })
    await user.click(tabSignUp())

    await user.type(email(), 'repetida@ejemplo.test')
    await user.type(password(), 'contrasena-larga')
    await user.type(passwordConfirm(), 'contrasena-larga')
    await user.click(submitSignUp())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Ya existe una cuenta con ese correo. Probá iniciar sesión.',
    )
    expect(email()).toHaveValue('repetida@ejemplo.test')
    // El botón vuelve a estar disponible: se puede corregir y reintentar.
    expect(submitSignUp()).toBeEnabled()
  })

  it('muestra el error del login', async () => {
    const user = userEvent.setup()
    renderScreen({
      signIn: vi.fn(async () => {
        throw new Error('Correo o contraseña incorrectos.')
      }),
    })

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'incorrecta')
    await user.click(submitSignIn())

    expect(await screen.findByRole('alert')).toHaveTextContent('Correo o contraseña incorrectos.')
  })

  it('al cambiar de modo se limpian los errores del modo anterior', async () => {
    const user = userEvent.setup()
    renderScreen({
      signIn: vi.fn(async () => {
        throw new Error('Correo o contraseña incorrectos.')
      }),
    })

    await user.type(email(), 'persona@ejemplo.test')
    await user.type(password(), 'incorrecta')
    await user.click(submitSignIn())
    await screen.findByRole('alert')

    await user.click(tabSignUp())

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('nunca deja la contraseña en el DOM como texto visible', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.type(password(), 'contrasena-larga')

    expect(password()).toHaveAttribute('type', 'password')
    expect(screen.queryByText('contrasena-larga')).not.toBeInTheDocument()
  })
})
