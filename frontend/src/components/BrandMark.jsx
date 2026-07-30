/**
 * Marca de Plata: un isotipo geométrico propio (una "P" construida con trazos rectos y un
 * arco) junto al logotipo tipográfico. Es original: no reutiliza activos de terceros.
 */
export default function BrandMark({ size = 'md', label = 'Plata' }) {
  return (
    <span className={`brand brand--${size}`}>
      <span className="brand__mark">
        <svg viewBox="0 0 32 32" role="img" aria-label={label}>
          <rect width="32" height="32" rx="10" fill="currentColor" />
          <path
            d="M12 8.5v15"
            stroke="#0d0f0d"
            strokeWidth="3"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M12 8.5h4.6a4.7 4.7 0 0 1 0 9.4H12"
            stroke="#0d0f0d"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </svg>
      </span>
      <span className="brand__word" aria-hidden="true">
        PLATA
      </span>
    </span>
  )
}
