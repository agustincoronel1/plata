"""Interfaz del proveedor de IA.

El gateway depende de este Protocol, no de un SDK concreto. Así el dominio queda
desacoplado: mock y proveedor real son intercambiables y ninguno filtra su implementación
hacia arriba.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class AIProviderResult:
    """Resultado crudo de una llamada al modelo.

    `parsed_output` es el objeto JSON (dict) que produjo el modelo, aún sin validar contra
    el schema de dominio: esa validación la hace el gateway, que es el único lugar donde un
    output inválido se convierte en `AIStructuredOutputError`. Los tokens y el request_id
    son opcionales (el mock no siempre los tiene).
    """

    parsed_output: dict[str, Any]
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


class AIProvider(Protocol):
    """Contrato mínimo de un proveedor. Solo sabe producir salida estructurada."""

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_input: str,
        response_schema: type[BaseModel],
        metadata: dict[str, str] | None = None,
    ) -> AIProviderResult:
        """Pide al modelo una salida que cumpla `response_schema` y la devuelve como dict.

        Debe traducir los errores propios (timeout, caída, credenciales) a las excepciones
        seguras de ``app.ai.exceptions``. Nunca propaga detalles del SDK ni la API key.
        """
        ...
