import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BackendGate from './BackendGate'
import BackendStatusProvider from './BackendStatusProvider'
import { RETRY_DELAYS_MS, retryDelayFor } from './coldStart'

/**
 * Arranque del backend en Render (plan gratuito).
 *
 * Cuando el servicio está dormido, la primera petición tarda hasta cerca de un minuto. Lo
 * que se prueba acá es que eso se cuente como "estamos iniciando el servidor" y no como
 * "no pudimos conectar", que los reintentos sean automáticos y acotados, que el contenido
 * se cargue solo al recuperarse —sin recargar la página— y que nada quede corriendo
 * después de desmontar.
 */

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, fetchApiHealth: vi.fn() }
})

const api = await import('../services/api')

const HEALTHY = { ok: true, version: '0.1.0', status: 200, waking: false }
const SLEEPING = { ok: false, status: 0, waking: true }
const BAD_GATEWAY = { ok: false, status: 502, waking: true }
const BROKEN = { ok: false, status: 500, waking: false }

const WAKING_TEXT = /Estamos iniciando el servidor de Vector\./
const FAILED_TEXT = /No pudimos iniciar el servidor\./

function mount() {
  return render(
    <BackendStatusProvider>
      <BackendGate>
        <p>Contenido protegido</p>
      </BackendGate>
    </BackendStatusProvider>,
  )
}

/** Deja pasar el tiempo del reintento número `attempt` (0 = el primero). */
async function esperarReintento(attempt) {
  await act(async () => {
    vi.advanceTimersByTime(retryDelayFor(attempt))
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  api.fetchApiHealth.mockReset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('backend disponible', () => {
  it('si /health responde 200 se muestra el contenido y no se habla de arranque', async () => {
    api.fetchApiHealth.mockResolvedValue(HEALTHY)
    mount()

    await act(async () => {})

    expect(screen.getByText('Contenido protegido')).toBeInTheDocument()
    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
  })

  it('no se consulta /health más de una vez cuando ya respondió', async () => {
    api.fetchApiHealth.mockResolvedValue(HEALTHY)
    mount()
    await act(async () => {})

    await act(async () => {
      vi.advanceTimersByTime(60000)
    })

    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
  })
})

describe('servidor dormido', () => {
  it('no muestra el contenido hasta que /health responda', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()

    await act(async () => {})

    expect(screen.queryByText('Contenido protegido')).not.toBeInTheDocument()
    expect(screen.getByText(WAKING_TEXT)).toBeInTheDocument()
  })

  it('un timeout o una red caída se cuentan como arranque, no como error', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()

    await act(async () => {})

    // El texto que se mostraba antes y describía mal lo que pasa.
    expect(screen.queryByText(/No pudimos conectar con el servidor/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Algo salió mal/)).not.toBeInTheDocument()
  })

  it('un 502 del proxy también es arranque', async () => {
    api.fetchApiHealth.mockResolvedValue(BAD_GATEWAY)
    mount()

    await act(async () => {})

    expect(screen.getByText(WAKING_TEXT)).toBeInTheDocument()
  })

  it('después de dos fallos se recupera solo y carga el contenido', async () => {
    api.fetchApiHealth
      .mockResolvedValueOnce(SLEEPING)
      .mockResolvedValueOnce(BAD_GATEWAY)
      .mockResolvedValue(HEALTHY)
    mount()

    await act(async () => {})
    expect(screen.getByText(WAKING_TEXT)).toBeInTheDocument()

    await esperarReintento(0)
    expect(screen.getByText(WAKING_TEXT)).toBeInTheDocument()

    await esperarReintento(1)

    // Sin recargar la página ni tocar nada: el contenido aparece solo.
    expect(screen.getByText('Contenido protegido')).toBeInTheDocument()
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(3)
  })

  it('informa cuántos reintentos lleva', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()

    await act(async () => {})
    expect(screen.getByText('Reintento 1…')).toBeInTheDocument()

    await esperarReintento(0)
    expect(screen.getByText('Reintentos: 2')).toBeInTheDocument()
  })

  it('los reintentos se espacian: no golpea el servidor cada segundo', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()
    await act(async () => {})

    const antes = api.fetchApiHealth.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(RETRY_DELAYS_MS[0] - 100)
    })

    expect(api.fetchApiHealth).toHaveBeenCalledTimes(antes)
  })
})

describe('reintentos agotados', () => {
  async function agotar() {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()
    await act(async () => {})

    for (let attempt = 0; attempt < RETRY_DELAYS_MS.length + 4; attempt += 1) {
      if (screen.queryByText(FAILED_TEXT)) break
      await esperarReintento(attempt)
    }
  }

  it('termina con un mensaje claro y deja de reintentar', async () => {
    await agotar()

    expect(screen.getByText(FAILED_TEXT)).toBeInTheDocument()
    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()

    const llamadas = api.fetchApiHealth.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(120000)
    })
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(llamadas)
  })

  it('el ciclo completo dura entre 60 y 90 segundos', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    const inicio = Date.now()
    mount()
    await act(async () => {})

    for (let attempt = 0; attempt < RETRY_DELAYS_MS.length + 4; attempt += 1) {
      if (screen.queryByText(FAILED_TEXT)) break
      await esperarReintento(attempt)
    }

    const duracion = Date.now() - inicio
    expect(duracion).toBeGreaterThanOrEqual(60000)
    expect(duracion).toBeLessThanOrEqual(90000)
  })

  it('el botón de reintento sigue disponible y vuelve a intentar', async () => {
    await agotar()
    const llamadas = api.fetchApiHealth.mock.calls.length
    api.fetchApiHealth.mockResolvedValue(HEALTHY)

    const boton = screen.getByRole('button', { name: 'Reintentar ahora' })
    await act(async () => {
      boton.click()
    })

    expect(api.fetchApiHealth.mock.calls.length).toBeGreaterThan(llamadas)
    expect(screen.getByText('Contenido protegido')).toBeInTheDocument()
  })
})

describe('un error real no es un arranque', () => {
  it('un 500 del backend no se reintenta ni dice "estamos iniciando"', async () => {
    api.fetchApiHealth.mockResolvedValue(BROKEN)
    mount()

    await act(async () => {})

    expect(screen.queryByText(WAKING_TEXT)).not.toBeInTheDocument()
    expect(screen.getByText(FAILED_TEXT)).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(120000)
    })
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
  })
})

describe('reintento manual mientras arranca', () => {
  it('el botón dispara un intento nuevo sin esperar al temporizador', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    mount()
    await act(async () => {})
    const llamadas = api.fetchApiHealth.mock.calls.length

    api.fetchApiHealth.mockResolvedValue(HEALTHY)
    await act(async () => {
      screen.getByRole('button', { name: 'Reintentar ahora' }).click()
    })

    expect(api.fetchApiHealth.mock.calls.length).toBe(llamadas + 1)
    expect(screen.getByText('Contenido protegido')).toBeInTheDocument()
  })
})

describe('limpieza al desmontar', () => {
  it('no quedan temporizadores ni sondeos después de desmontar', async () => {
    api.fetchApiHealth.mockResolvedValue(SLEEPING)
    const { unmount } = mount()
    await act(async () => {})
    const llamadas = api.fetchApiHealth.mock.calls.length

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(300000)
    })

    expect(api.fetchApiHealth).toHaveBeenCalledTimes(llamadas)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('una respuesta que llega después de desmontar no rompe nada', async () => {
    let resolver
    api.fetchApiHealth.mockReturnValue(
      new Promise((resolve) => {
        resolver = resolve
      }),
    )
    const { unmount } = mount()

    unmount()
    await act(async () => {
      resolver(HEALTHY)
    })

    // No hay estado que actualizar ni temporizador que programar: el ciclo ya se invalidó.
    expect(api.fetchApiHealth).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})
