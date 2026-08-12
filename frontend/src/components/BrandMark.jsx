/**
 * Marca de Vector: isotipo geométrico propio junto al logotipo tipográfico. Es original: no
 * reutiliza activos de terceros.
 *
 * El isotipo son tres elementos y ninguno es un cliché financiero (nada de monedas, billetes
 * ni signos de peso):
 *
 * - un PUNTO DE ORIGEN, dónde estás hoy;
 * - un TRAZO DIAGONAL que sale de ese punto, la dirección y la magnitud (un vector);
 * - una PUNTA EN ESCUADRA abierta, el destino.
 *
 * Forma una "V" implícita en el ángulo, pero construida con trazos y un nodo en lugar de una
 * letra suelta. El contenedor usa `currentColor`, así que la marca toma el color del contexto
 * y funciona igual en la barra lateral que sobre el fondo oscuro.
 *
 * El mismo dibujo está duplicado en `public/favicon.svg`, que es un archivo estático servido
 * por el navegador y no puede importar este componente. Si se cambia uno, cambiar el otro.
 */
export default function BrandMark({ size = 'md', label = 'Vector' }) {
  return (
    <span className={`brand brand--${size}`}>
      <span className="brand__mark">
        <svg viewBox="0 0 32 32" role="img" aria-label={label}>
          <rect width="32" height="32" rx="10" fill="currentColor" />
          <circle cx="10.5" cy="21.5" r="2.6" fill="#0d0f0d" />
          <path
            d="M12.6 19.4 21.4 10.6"
            stroke="#0d0f0d"
            strokeWidth="3"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M15.8 9.4h6.8v6.8"
            stroke="#0d0f0d"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </svg>
      </span>
      <span className="brand__word" aria-hidden="true">
        VECTOR
      </span>
    </span>
  )
}
