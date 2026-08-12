import '@testing-library/jest-dom/vitest'

// jsdom no implementa el elemento <dialog> nativo (showModal / close). Como los diálogos
// de Vector se apoyan en <dialog>, aportamos una implementación mínima para los tests.
if (typeof HTMLDialogElement !== 'undefined') {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true
    }
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.open = false
      this.dispatchEvent(new Event('close'))
    }
  }
}
