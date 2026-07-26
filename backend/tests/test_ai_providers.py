"""Tests del proveedor mock y del contrato del proveedor real (sin llamadas reales)."""

import pytest

from app.ai.exceptions import AIProviderTimeoutError, AIProviderUnavailableError
from app.ai.providers.mock import MockAIProvider
from app.core.config import Settings
from app.schemas.ai_transaction import TransactionParseModelOutput

AS_OF = {"as_of": "2026-07-24"}


def _call(provider: MockAIProvider, text: str):
    return provider.generate_structured(
        system_prompt="sys",
        user_input=text,
        response_schema=TransactionParseModelOutput,
        metadata=AS_OF,
    )


def test_mock_matchea_gasto_conocido() -> None:
    result = _call(MockAIProvider(), "Gasté 25 lucas ayer en nafta con débito")
    assert result.provider == "mock"
    assert result.parsed_output["intent"] == "create_transaction"
    assert result.parsed_output["transaction"]["amount"] == "25000.00"
    assert result.parsed_output["transaction"]["occurred_on"] == "2026-07-23"


def test_mock_texto_desconocido_es_unknown() -> None:
    result = _call(MockAIProvider(), "asdkjfh qwer zxcv")
    assert result.parsed_output["intent"] == "unknown"
    assert result.parsed_output["transaction"] is None


def test_mock_force_timeout() -> None:
    with pytest.raises(AIProviderTimeoutError):
        _call(MockAIProvider(force="timeout"), "cualquier cosa")


def test_mock_force_error() -> None:
    with pytest.raises(AIProviderUnavailableError):
        _call(MockAIProvider(force="error"), "cualquier cosa")


def test_mock_force_invalid_devuelve_confidence_fuera_de_rango() -> None:
    result = _call(MockAIProvider(force="invalid"), "cualquier cosa")
    assert result.parsed_output["confidence"] == "5"


def test_mock_frases_centinela_fuerzan_fallo() -> None:
    with pytest.raises(AIProviderTimeoutError):
        _call(MockAIProvider(), "forzar timeout ahora")
    with pytest.raises(AIProviderUnavailableError):
        _call(MockAIProvider(), "forzar error ahora")


def test_mock_respuesta_configurada_explicita() -> None:
    configured = {
        "intent": "create_transaction",
        "transaction": None,
        "confidence": "0.42",
        "missing_fields": [],
        "ambiguities": [],
        "explanation": "inyectada",
    }
    provider = MockAIProvider(responses={"un texto puntual": configured})
    result = _call(provider, "Un texto puntual")
    assert result.parsed_output["explanation"] == "inyectada"
    assert result.parsed_output["confidence"] == "0.42"


def test_mock_es_deterministico() -> None:
    a = _call(MockAIProvider(), "Cobré 1.200.000 del sueldo")
    b = _call(MockAIProvider(), "Cobré 1.200.000 del sueldo")
    assert a.parsed_output == b.parsed_output


def test_openai_sin_api_key_falla_solo_al_usarse() -> None:
    # Construir el proveedor real sin key NO debe fallar (no bloquea el arranque)...
    from app.ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(Settings(ai_provider="openai", ai_api_key=""))
    # ...pero usarlo sin key sí, con un error seguro (sin filtrar nada, sin tocar el SDK).
    with pytest.raises(AIProviderUnavailableError):
        provider.generate_structured(
            system_prompt="sys",
            user_input="hola",
            response_schema=TransactionParseModelOutput,
            metadata=AS_OF,
        )
