import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import BrandMark from './components/BrandMark'

/**
 * La marca visible es Vector.
 *
 * Estos tests miran lo que la persona LEE: el título de la pestaña, el logotipo y el
 * isotipo. Un rebranding a medias se nota justo acá —un favicon viejo, un `aria-label` que
 * quedó con el nombre anterior— y no lo detecta ningún test funcional, porque la aplicación
 * sigue andando igual.
 *
 * "plata" en minúscula, cuando significa dinero, no es la marca y se conserva: por eso el
 * barrido de abajo busca la forma capitalizada.
 */

const RAIZ = resolve(__dirname, '..')
const leer = (ruta) => readFileSync(resolve(RAIZ, ruta), 'utf8')

const MARCA_VIEJA = /\bPlata\b|\bPLATA\b/

describe('identidad de Vector', () => {
  it('el título del navegador usa Vector', () => {
    const html = leer('index.html')

    expect(html).toContain('<title>Vector — Tu copiloto financiero</title>')
    expect(MARCA_VIEJA.test(html)).toBe(false)
  })

  it('la metadata social describe a Vector', () => {
    const html = leer('index.html')

    expect(html).toContain('content="Vector"')
    expect(html).toContain('Tus finanzas, en la dirección correcta.')
    expect(html).toContain('Tu copiloto inteligente de finanzas personales.')
  })

  it('el logotipo dice VECTOR y el isotipo se anuncia como Vector', () => {
    render(<BrandMark />)

    expect(screen.getByText('VECTOR')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Vector' })).toBeInTheDocument()
  })

  it('el isotipo es el vector: punto de origen, trazo y punta', () => {
    const { container } = render(<BrandMark />)
    const svg = container.querySelector('svg')

    // Punto de origen.
    expect(svg.querySelector('circle')).not.toBeNull()
    // Trazo diagonal + punta en escuadra.
    expect(svg.querySelectorAll('path')).toHaveLength(2)
  })

  it('el favicon acompaña al isotipo del componente', () => {
    const favicon = leer('public/favicon.svg')

    expect(favicon).toContain('<title>Vector</title>')
    expect(favicon).toContain('<circle')
    // El dibujo está duplicado a propósito (un .svg estático no puede importar JSX): si se
    // cambia uno y no el otro, la pestaña queda con una marca distinta de la de la interfaz.
    expect(favicon).toContain('M12.6 19.4 21.4 10.6')
  })

  it('no queda ningún rastro del isotipo anterior', () => {
    const favicon = leer('public/favicon.svg')
    const marca = leer('src/components/BrandMark.jsx')

    // La "P" del logo viejo se dibujaba con estos dos trazos.
    for (const trazoViejo of ['M12 8.5v15', 'M12 8.5h4.6a4.7 4.7 0 0 1 0 9.4H12']) {
      expect(favicon).not.toContain(trazoViejo)
      expect(marca).not.toContain(trazoViejo)
    }
  })

  it('ningún archivo del frontend presenta la marca vieja', () => {
    const archivos = import.meta.glob('./**/*.{js,jsx,css}', {
      eager: true,
      query: '?raw',
      import: 'default',
    })

    const ofensores = Object.entries(archivos)
      .filter(([ruta]) => !ruta.includes('branding.test'))
      .filter(([, contenido]) => MARCA_VIEJA.test(contenido))
      .map(([ruta]) => ruta)

    expect(ofensores).toEqual([])
  })
})
