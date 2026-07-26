"""AI Gateway: orquesta una interpretación de extremo a extremo.

Responsabilidades:

1. Elegir el proveedor según la configuración (mock por defecto).
2. Cargar el prompt versionado y renderizarlo con la fecha de referencia.
3. Llamar al proveedor (que ya traduce sus errores a excepciones seguras).
4. **Validar** el structured output contra el schema de dominio. Una salida que no cumple
   el contrato se convierte en `AIStructuredOutputError` (nunca se filtra la respuesta
   cruda).

El gateway depende de la **interfaz** `AIProvider`, no de un proveedor concreto. Se puede
inyectar un proveedor (para tests) o dejar que se construya desde la config.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from app.ai.exceptions import AIProviderUnavailableError, AIStructuredOutputError
from app.ai.prompts import (
    TRANSACTION_PARSER,
    TRANSACTION_PARSER_VERSION,
    get_prompt,
)
from app.ai.providers.base import AIProvider
from app.ai.providers.mock import MockAIProvider
from app.core.config import Settings, settings
from app.schemas.ai_transaction import TransactionParseModelOutput

ALLOWED_PROVIDERS = ("mock", "openai")


def build_provider(config: Settings | None = None) -> AIProvider:
    """Construye el proveedor configurado. No hace red ni valida credenciales todavía.

    Un proveedor real mal configurado (sin API key) se construye igual: falla recién al
    usarse. Un nombre de proveedor desconocido es un error seguro (503) en tiempo de uso,
    no un crash al arrancar.
    """
    config = config or settings
    provider = config.ai_provider
    if provider == "mock":
        return MockAIProvider()
    if provider == "openai":
        # Import perezoso: no se toca el SDK salvo que el proveedor sea openai.
        from app.ai.providers.openai import OpenAIProvider

        return OpenAIProvider(config)
    raise AIProviderUnavailableError(
        "El proveedor de IA configurado no es válido. Revisá AI_PROVIDER."
    )


@dataclass(frozen=True)
class ParseResult:
    """Salida validada del gateway + metadatos de trazabilidad."""

    output: TransactionParseModelOutput
    provider: str
    model: str
    latency_ms: int
    prompt_version: str
    prompt_checksum: str
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None


class AIGateway:
    """Orquestador. Recibe un proveedor por la interfaz; el resto es determinista."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    def parse_transaction(self, *, source_text: str, as_of: date, trace_id: str) -> ParseResult:
        prompt = get_prompt(TRANSACTION_PARSER, TRANSACTION_PARSER_VERSION)
        system_prompt = prompt.render(as_of)

        result = self._provider.generate_structured(
            system_prompt=system_prompt,
            user_input=source_text,
            response_schema=TransactionParseModelOutput,
            metadata={
                "task": TRANSACTION_PARSER,
                "prompt_version": prompt.version,
                "as_of": as_of.isoformat(),
                "trace_id": trace_id,
            },
        )

        try:
            output = TransactionParseModelOutput.model_validate(result.parsed_output)
        except ValidationError as exc:
            # No incluimos el contenido inválido: solo un error seguro.
            raise AIStructuredOutputError from exc

        return ParseResult(
            output=output,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            prompt_version=prompt.version,
            prompt_checksum=prompt.checksum,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=result.request_id,
        )


def get_ai_gateway() -> AIGateway:
    """Dependencia de FastAPI: gateway con el proveedor de la config.

    En tests se puede sobreescribir con `app.dependency_overrides` para inyectar un mock
    que fuerce timeouts, errores o salidas inválidas.
    """
    return AIGateway(build_provider())
