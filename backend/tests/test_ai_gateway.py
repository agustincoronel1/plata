"""Tests del AI Gateway y del prompt registry."""

from datetime import date

import pytest

from app.ai.exceptions import AIProviderUnavailableError, AIStructuredOutputError
from app.ai.gateway import AIGateway, build_provider
from app.ai.prompts import TRANSACTION_PARSER, TRANSACTION_PARSER_VERSION, get_prompt
from app.ai.providers.mock import MockAIProvider
from app.core.config import Settings

AS_OF = date(2026, 7, 24)


def _gateway(provider) -> AIGateway:
    return AIGateway(provider)


def test_build_provider_mock() -> None:
    assert build_provider(Settings(ai_provider="mock")).__class__.__name__ == "MockAIProvider"


def test_build_provider_desconocido_es_error_seguro() -> None:
    with pytest.raises(AIProviderUnavailableError):
        build_provider(Settings(ai_provider="inexistente"))


def test_gateway_parse_valido() -> None:
    result = _gateway(MockAIProvider()).parse_transaction(
        source_text="Gasté 25 lucas ayer en nafta con débito", as_of=AS_OF, trace_id="t1"
    )
    assert result.output.intent.value == "create_transaction"
    assert result.prompt_version == TRANSACTION_PARSER_VERSION
    assert result.prompt_checksum


def test_gateway_salida_invalida_es_structured_output_error() -> None:
    with pytest.raises(AIStructuredOutputError):
        _gateway(MockAIProvider(force="invalid")).parse_transaction(
            source_text="cualquier cosa", as_of=AS_OF, trace_id="t2"
        )


def test_prompt_registry_carga_y_renderiza() -> None:
    prompt = get_prompt(TRANSACTION_PARSER, TRANSACTION_PARSER_VERSION)
    assert prompt.checksum == get_prompt(TRANSACTION_PARSER, TRANSACTION_PARSER_VERSION).checksum
    rendered = prompt.render(AS_OF)
    assert "2026-07-24" in rendered
    assert "{{AS_OF}}" not in rendered
