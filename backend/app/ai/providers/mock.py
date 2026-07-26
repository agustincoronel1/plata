"""Proveedor mock: determinístico, sin costes y sin API key.

Sirve para desarrollo, tests, demo y evaluaciones offline. NO es un parser de IA ni un
parser de regex de producción: es una tabla de escenarios controlados (fixtures JSON
versionadas) para probar el *pipeline* completo (gateway, validación, borrador,
confirmación), no para reemplazar al modelo real.

El emparejamiento es por texto normalizado (minúsculas, sin acentos, espacios colapsados)
contra las claves de la fixture. Las fechas relativas ("hoy", "ayer", "anteayer") se
resuelven contra `as_of`, que llega en `metadata`. Cualquier texto no reconocido cae en
`unknown` con baja confianza.

Se pueden inyectar respuestas explícitas (`responses={clave_normalizada: output}`) y forzar
fallos (timeout, error del proveedor, salida inválida) por constructor o por frase
centinela, para ejercitar las ramas de error sin un proveedor real.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.ai.exceptions import AIProviderTimeoutError, AIProviderUnavailableError
from app.ai.providers.base import AIProviderResult

MOCK_PROVIDER_NAME = "mock"
MOCK_MODEL_NAME = "mock-transaction-parser-v1"
_MOCK_LATENCY_MS = 3

FORCE_TIMEOUT = "timeout"
FORCE_ERROR = "error"
FORCE_INVALID = "invalid"

_FIXTURES = Path(__file__).parent / "fixtures" / "transaction_cases.json"


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos y sin puntuación separadora, con espacios colapsados.

    Conserva los puntos entre dígitos (p. ej. "1.200.000", separadores de miles) y solo
    elimina los de fin de oración (seguidos de espacio o del final).
    """
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_accents = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    cleaned = re.sub(r"[,¿?¡!;:]", " ", without_accents)
    cleaned = re.sub(r"\.(?=\s|$)", " ", cleaned)
    return " ".join(cleaned.split())


def _resolve_date(date_key: str | None, as_of: date) -> str | None:
    """Traduce 'today'/'yesterday'/'day_before' a una fecha ISO relativa a `as_of`."""
    if date_key == "today":
        return as_of.isoformat()
    if date_key == "yesterday":
        return (as_of - timedelta(days=1)).isoformat()
    if date_key == "day_before":
        return (as_of - timedelta(days=2)).isoformat()
    return None


@lru_cache(maxsize=1)
def _load_fixtures() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Carga (cacheado) las fixtures JSON: índice por clave normalizada + caso unknown."""
    data = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    index: dict[str, dict[str, Any]] = {}
    for case in data["cases"]:
        for key in case["keys"]:
            index[key] = case
    return index, data["unknown"]


def _build_output(case: dict[str, Any], as_of: date, source_text: str) -> dict[str, Any]:
    """Construye el dict de salida estructurada a partir de un escenario de fixture."""
    if case["intent"] == "unknown":
        return {
            "intent": "unknown",
            "transaction": None,
            "confidence": case["confidence"],
            "missing_fields": case.get("missing", []),
            "ambiguities": case.get("ambiguities", []),
            "explanation": case.get("explanation", ""),
        }

    transaction = {
        "type": case["type"],
        "amount": case.get("amount"),
        "category": case.get("category"),
        "description": case.get("description"),
        "occurred_on": _resolve_date(case.get("date"), as_of),
        "payment_method": case.get("payment"),
        "currency": case.get("currency", "ARS"),
        "source_text": source_text,
    }
    return {
        "intent": "create_transaction",
        "transaction": transaction,
        "confidence": case["confidence"],
        "missing_fields": case.get("missing", []),
        "ambiguities": case.get("ambiguities", []),
        "explanation": case.get("explanation", ""),
    }


class MockAIProvider:
    """Implementación de `AIProvider` sin coste ni red. Cumple el mismo contrato.

    `responses` permite inyectar salidas ya construidas por clave normalizada (tests que
    necesitan un output puntual). `force` obliga un modo de fallo en cada llamada.
    """

    def __init__(
        self,
        *,
        force: str | None = None,
        responses: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._force = force
        self._responses = {_normalize(k): v for k, v in (responses or {}).items()}

    def _resolve_force(self, normalized: str) -> str | None:
        if self._force is not None:
            return self._force
        if "forzar timeout" in normalized:
            return FORCE_TIMEOUT
        if "forzar error" in normalized:
            return FORCE_ERROR
        if "forzar invalido" in normalized or "respuesta invalida" in normalized:
            return FORCE_INVALID
        return None

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_input: str,
        response_schema: type[BaseModel],
        metadata: dict[str, str] | None = None,
    ) -> AIProviderResult:
        normalized = _normalize(user_input)

        force = self._resolve_force(normalized)
        if force == FORCE_TIMEOUT:
            raise AIProviderTimeoutError
        if force == FORCE_ERROR:
            raise AIProviderUnavailableError
        if force == FORCE_INVALID:
            # confidence fuera de rango: el gateway lo convierte en AIStructuredOutputError.
            return self._result(
                {
                    "intent": "create_transaction",
                    "transaction": None,
                    "confidence": "5",
                    "missing_fields": [],
                    "ambiguities": [],
                    "explanation": "",
                }
            )

        if normalized in self._responses:
            return self._result(self._responses[normalized])

        as_of_raw = (metadata or {}).get("as_of")
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else date.today()
        index, unknown = _load_fixtures()
        case = index.get(normalized, unknown)
        output = _build_output(case, as_of, user_input)
        return self._result(
            output,
            input_tokens=len(user_input.split()),
            output_tokens=len(output.get("explanation", "").split()),
        )

    def _result(
        self,
        output: dict[str, Any],
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> AIProviderResult:
        return AIProviderResult(
            parsed_output=output,
            provider=MOCK_PROVIDER_NAME,
            model=MOCK_MODEL_NAME,
            latency_ms=_MOCK_LATENCY_MS,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
