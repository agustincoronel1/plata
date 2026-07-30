import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Manrope autoalojada: sin pedidos a servidores externos en tiempo de ejecución.
import '@fontsource-variable/manrope'

import './styles/global.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
