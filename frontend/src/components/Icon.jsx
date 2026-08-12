/**
 * Set de iconos lineales propios de Vector, en SVG y con `currentColor`.
 *
 * Son decorativos: el significado siempre está en el texto o en el `aria-label` del botón
 * que los contiene, así que se marcan con aria-hidden. No se usan emojis como iconos.
 */

const PATHS = {
  home: <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1z" />,
  receipt: (
    <>
      <path d="M6 3.5h12v17l-2.4-1.6-2.4 1.6-2.4-1.6L8.4 20.5 6 18.9z" />
      <path d="M9.5 9h5M9.5 13h5" />
    </>
  ),
  calendar: (
    <>
      <rect x="3.5" y="5.5" width="17" height="15" rx="3" />
      <path d="M3.5 10h17M8.5 3.5v4M15.5 3.5v4" />
    </>
  ),
  calculator: (
    <>
      <rect x="4.5" y="3.5" width="15" height="17" rx="3" />
      <path d="M8 8h8M8.5 13h1.5M14 13h1.5M8.5 16.5h1.5M14 16.5h1.5" />
    </>
  ),
  sparkle: (
    <>
      <path d="m12 3.5 1.9 4.9 4.9 1.9-4.9 1.9L12 17.1l-1.9-4.9-4.9-1.9 4.9-1.9z" />
      <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.8 20c.9-3.5 3.7-5.4 7.2-5.4s6.3 1.9 7.2 5.4" />
    </>
  ),
  arrowUp: <path d="M12 19V5.5M6 11.5 12 5.5l6 6" />,
  arrowDown: <path d="M12 5v13.5M18 12.5 12 18.5l-6-6" />,
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  pencil: (
    <>
      <path d="M4.5 19.5h3.2L18.4 8.8a2.3 2.3 0 0 0-3.2-3.2L4.5 16.3z" />
      <path d="m13.8 7 3.2 3.2" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 6.5h15M9.5 6.5V4.8a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.7" />
      <path d="M6.8 6.5 7.7 20a1 1 0 0 0 1 .9h6.6a1 1 0 0 0 1-.9l.9-13.5" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  send: <path d="M4.5 12 20 5l-4.2 15-4-6.2z" />,
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  chevronRight: <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />,
  shield: <path d="M12 3.5 19 6v6c0 4-2.9 7.3-7 8.5-4.1-1.2-7-4.5-7-8.5V6z" />,
  alert: (
    <>
      <path d="M12 4.5 21 20H3z" />
      <path d="M12 10v4M12 17h.01" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7v5.3l3.2 2" />
    </>
  ),
  wallet: (
    <>
      <path d="M4 8.5a2.5 2.5 0 0 1 2.5-2.5H17a2.5 2.5 0 0 1 2.5 2.5v8A2.5 2.5 0 0 1 17 19H6.5A2.5 2.5 0 0 1 4 16.5z" />
      <path d="M4 9.5h13.5M16 13h1.5" />
    </>
  ),
  logout: (
    <>
      <path d="M15 7.5V5.8a1.8 1.8 0 0 0-1.8-1.8H6.3A1.8 1.8 0 0 0 4.5 5.8v12.4A1.8 1.8 0 0 0 6.3 20h6.9a1.8 1.8 0 0 0 1.8-1.8v-1.7" />
      <path d="M10.5 12h9M16.5 8.5 20 12l-3.5 3.5" />
    </>
  ),
}

export default function Icon({ name, className }) {
  const shape = PATHS[name]
  if (!shape) {
    return null
  }

  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {shape}
    </svg>
  )
}
