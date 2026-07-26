"""Excepciones seguras del subsistema de IA.

Cada una lleva un ``status_code`` HTTP y un ``detail`` ya pensado para el usuario. Nunca
transportan el prompt, la respuesta cruda del modelo, la API key, headers, variables de
entorno ni detalles del SDK: solo un texto mostrable. La capa de API las traduce con un
único manejador (ver app.main).
"""

from __future__ import annotations


class AIError(Exception):
    """Base de todos los errores del flujo de IA. Trae un código HTTP y un detalle seguro."""

    status_code = 500
    default_detail = "Ocurrió un error con la interpretación por IA. Probá el formulario manual."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class AIProviderUnavailableError(AIError):
    """El proveedor de IA no está disponible o está mal configurado (p. ej. sin API key)."""

    status_code = 503
    default_detail = (
        "El servicio de IA no está disponible en este momento. Podés cargar el movimiento "
        "con el formulario manual."
    )


class AIProviderTimeoutError(AIError):
    """El proveedor no respondió dentro del tiempo permitido."""

    status_code = 504
    default_detail = (
        "La interpretación por IA tardó demasiado. Probá de nuevo o usá el formulario manual."
    )


class AIStructuredOutputError(AIError):
    """El modelo devolvió algo que no cumple el contrato estructurado esperado."""

    status_code = 502
    default_detail = (
        "No pudimos interpretar una respuesta válida de la IA. Usá el formulario manual."
    )


class AILowConfidenceError(AIError):
    """La confianza del modelo quedó por debajo del mínimo aceptable (si se usa como corte)."""

    status_code = 422
    default_detail = "La IA no está segura de haber entendido. Revisá o cargá el dato a mano."


class AIDraftValidationError(AIError):
    """El borrador (con o sin correcciones) no forma un movimiento válido para confirmar.

    Lleva `errors` en el formato campo→mensaje de FastAPI para que el frontend pueda
    ubicar el problema junto al input. Nunca incluye el valor crudo del campo.
    """

    status_code = 422
    default_detail = "Revisá los datos del movimiento antes de confirmar."

    def __init__(
        self, errors: list[dict[str, object]] | None = None, detail: str | None = None
    ) -> None:
        self.errors = errors or []
        super().__init__(detail)


# --- Errores del borrador (human-in-the-loop) ---


class DraftNotFoundError(AIError):
    """El borrador pedido no existe (nunca se creó o el backend se reinició)."""

    status_code = 404
    default_detail = "No encontramos ese borrador. Volvé a interpretar el texto."


class DraftExpiredError(AIError):
    """El borrador venció por TTL: hay que interpretar de nuevo."""

    status_code = 410
    default_detail = "El borrador expiró. Volvé a interpretar el texto para confirmarlo."


class DraftAlreadyUsedError(AIError):
    """El borrador ya fue confirmado o rechazado: no puede reutilizarse."""

    status_code = 409
    default_detail = "Ese borrador ya fue usado. Interpretá el texto de nuevo si hace falta."
