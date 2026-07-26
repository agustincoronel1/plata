"""Proveedor real sobre el SDK oficial de OpenAI, detrás de la interfaz común.

Decisiones:

- El SDK se importa de forma **perezosa** dentro del método: si `AI_PROVIDER=mock`, este
  archivo se puede importar sin tener `openai` instalado y sin API key.
- Se usa la salida estructurada nativa (`responses.parse` con un modelo Pydantic), no
  "texto libre + json.loads".
- Sin API key, falla **al usarse** (no al construirse ni al arrancar la app), con una
  excepción segura.
- Los errores del SDK se traducen a las excepciones internas; nunca se propaga el prompt,
  la respuesta cruda, la API key, headers ni detalles del SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.ai.exceptions import (
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIStructuredOutputError,
)
from app.ai.providers.base import AIProviderResult

if TYPE_CHECKING:
    from app.core.config import Settings

OPENAI_PROVIDER_NAME = "openai"


class OpenAIProvider:
    """Implementación real. Construirla nunca falla; la validación ocurre al llamar."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.ai_model
        self._api_key = settings.ai_api_key
        self._timeout = settings.ai_timeout_seconds
        self._max_retries = settings.ai_max_retries

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_input: str,
        response_schema: type[BaseModel],
        metadata: dict[str, str] | None = None,
    ) -> AIProviderResult:
        if not self._api_key:
            # Falla claramente recién al iniciar la operación de IA, no al arrancar.
            raise AIProviderUnavailableError(
                "El proveedor de IA real no tiene API key configurada. "
                "Configurá AI_API_KEY o usá AI_PROVIDER=mock."
            )

        client, api_error, api_timeout = _load_sdk()
        instance = client(
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

        try:
            import time

            started = time.monotonic()
            completion = instance.responses.parse(
                model=self._model,
                input=[
                    {"role": "system", "content": system_prompt},
                    # El texto del usuario viaja como DATO de rol user, nunca como sistema.
                    {"role": "user", "content": user_input},
                ],
                text_format=response_schema,
                metadata=metadata or {},
            )
            latency_ms = int((time.monotonic() - started) * 1000)
        except api_timeout as exc:  # pragma: no cover - requiere SDK y red
            raise AIProviderTimeoutError from exc
        except api_error as exc:  # pragma: no cover - requiere SDK y red
            raise AIProviderUnavailableError from exc
        except Exception as exc:  # pragma: no cover - defensivo
            raise AIProviderUnavailableError from exc

        parsed = completion.output_parsed
        if parsed is None:  # pragma: no cover - requiere SDK y red
            raise AIStructuredOutputError

        usage = getattr(completion, "usage", None)
        return AIProviderResult(
            parsed_output=parsed.model_dump(mode="json"),
            provider=OPENAI_PROVIDER_NAME,
            model=self._model,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            request_id=getattr(completion, "id", None),
        )


def _load_sdk() -> tuple[Any, type[Exception], type[Exception]]:
    """Importa el SDK de forma perezosa. Ausente => proveedor no disponible (seguro)."""
    try:
        from openai import APITimeoutError, OpenAI, OpenAIError
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise AIProviderUnavailableError(
            "El proveedor de IA real no está instalado. Usá AI_PROVIDER=mock."
        ) from exc
    return OpenAI, OpenAIError, APITimeoutError
