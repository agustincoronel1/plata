from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.ai.agent.brain import OpenAIAgentBrain, _tool_specs
from app.ai.agent.schemas import AgentIntent
from app.ai.agent.tools import ToolContext
from app.ai.exceptions import (
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIStructuredOutputError,
)
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.services.draft_store import DraftStatus, InMemoryDraftStore


class FakeTimeout(Exception):
    pass


class FakeAPIError(Exception):
    pass


def _settings(**overrides):
    data = {
        "ai_model": "gpt-test",
        "ai_api_key": "sk-test",
        "ai_timeout_seconds": 7,
        "ai_max_retries": 2,
        "ai_agent_max_iterations": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _call(name: str, call_id: str, arguments: str):
    return SimpleNamespace(
        type="function_call",
        name=name,
        call_id=call_id,
        arguments=arguments,
    )


def _final(answer: str):
    return SimpleNamespace(output=[], output_parsed={"answer": answer})


def _patch_client(monkeypatch, responses):
    records = []

    class FakeResponses:
        def parse(self, **kwargs):
            records.append(kwargs)
            item = responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.responses = FakeResponses()

    monkeypatch.setattr(
        "app.ai.agent.brain._load_openai_sdk",
        lambda: (FakeOpenAI, FakeAPIError, FakeTimeout),
    )
    return records


def _ctx():
    return ToolContext(
        session=None,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=date(2026, 7, 24),
    )


def _draft_count(ctx) -> int:
    return len(ctx.draft_store._drafts)


def test_openai_brain_final_answer_without_tools(monkeypatch):
    records = _patch_client(monkeypatch, [_final("Respuesta grounded.")])

    result = OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())

    assert result["final_answer"] == "Respuesta grounded."
    assert result["tool_calls"] == []
    assert records[0]["store"] is False


def test_openai_brain_one_tool_preserves_call_id_and_output(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _call(
                        "create_transaction_draft",
                        "call_1",
                        '{"text":"Gasté 25 lucas ayer en nafta con débito"}',
                    )
                ],
                output_parsed=None,
            ),
            _final("Preparé un draft."),
        ],
    )
    ctx = _ctx()

    result = OpenAIAgentBrain(_settings()).run_agentic("registrá nafta", [], ctx)

    assert result["intent"] == AgentIntent.CREATE_TRANSACTION
    assert result["tool_calls"][0]["call_id"] == "call_1"
    assert result["pending_action"]["kind"] == "create_transaction"
    assert (
        ctx.draft_store.get(UUID(result["pending_action"]["draft_id"])).status
        is DraftStatus.PENDING
    )
    second_input = records[1]["input"]
    output = [item for item in second_input if item.get("type") == "function_call_output"][0]
    assert output["call_id"] == "call_1"
    assert "create_transaction_draft" in output["output"]


def test_openai_brain_two_tools_same_round(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _call(
                        "create_transaction_draft",
                        "call_tx",
                        '{"text":"Gasté 25 lucas ayer en nafta con débito"}',
                    ),
                    _call(
                        "create_commitment_draft",
                        "call_cm",
                        '{"name":"alquiler","amount":"350000","due_date":"2026-08-05","category":"vivienda"}',
                    ),
                ],
                output_parsed=None,
            ),
            _final("Hay drafts pendientes."),
        ],
    )
    ctx = _ctx()

    result = OpenAIAgentBrain(_settings()).run_agentic("dos acciones", [], ctx)

    assert [call["call_id"] for call in result["tool_calls"]] == ["call_tx", "call_cm"]
    assert result["pending_action"]["kind"] == "create_transaction"
    assert _draft_count(ctx) == 1
    blocked = result["tool_results"][1]
    assert blocked["ok"] is False
    assert blocked["error"] == "multiple_sensitive_actions_not_allowed"
    outputs = [item for item in records[1]["input"] if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in outputs] == ["call_tx", "call_cm"]
    assert "multiple_sensitive_actions_not_allowed" in outputs[1]["output"]


def test_openai_brain_incomplete_write_blocks_second_write(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _call(
                        "create_transaction_draft",
                        "call_tx",
                        '{"text":"Gaste algo en el super"}',
                    ),
                    _call(
                        "create_commitment_draft",
                        "call_cm",
                        '{"name":"alquiler","amount":"350000","due_date":"2026-08-05","category":"vivienda"}',
                    ),
                ],
                output_parsed=None,
            ),
            _final("Falta completar el movimiento."),
        ],
    )
    ctx = _ctx()

    result = OpenAIAgentBrain(_settings()).run_agentic("dos acciones", [], ctx)

    assert [call["call_id"] for call in result["tool_calls"]] == ["call_tx", "call_cm"]
    assert result["pending_action"] is None
    assert result["approval_required"] is False
    assert _draft_count(ctx) == 1
    assert result["tool_results"][0]["ok"] is True
    assert result["tool_results"][0]["data"]["is_confirmable"] is False
    blocked = result["tool_results"][1]
    assert blocked["ok"] is False
    assert blocked["error"] == "multiple_sensitive_actions_not_allowed"
    outputs = [item for item in records[1]["input"] if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in outputs] == ["call_tx", "call_cm"]
    assert "multiple_sensitive_actions_not_allowed" in outputs[1]["output"]


def test_openai_brain_second_tool_after_function_output(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _call(
                        "create_transaction_draft",
                        "call_tx",
                        '{"text":"Gasté 25 lucas ayer en nafta con débito"}',
                    )
                ],
                output_parsed=None,
            ),
            SimpleNamespace(
                output=[
                    _call(
                        "create_commitment_draft",
                        "call_cm",
                        '{"name":"alquiler","amount":"350000","due_date":"2026-08-05","category":"vivienda"}',
                    )
                ],
                output_parsed=None,
            ),
            _final("Listo."),
        ],
    )

    ctx = _ctx()
    result = OpenAIAgentBrain(_settings()).run_agentic("multi step", [], ctx)

    assert [call["call_id"] for call in result["tool_calls"]] == ["call_tx", "call_cm"]
    assert result["pending_action"]["kind"] == "create_transaction"
    assert _draft_count(ctx) == 1
    assert result["tool_results"][1]["error"] == "multiple_sensitive_actions_not_allowed"
    assert any(item.get("call_id") == "call_tx" for item in records[1]["input"])


def test_openai_tool_specs_are_strict():
    assert all(spec["strict"] is True for spec in _tool_specs())


def test_openai_brain_rejects_unknown_tool(monkeypatch):
    _patch_client(
        monkeypatch,
        [SimpleNamespace(output=[_call("drop_table", "call_bad", "{}")], output_parsed=None)],
    )

    with pytest.raises(AIStructuredOutputError):
        OpenAIAgentBrain(_settings()).run_agentic("rompé todo", [], _ctx())


def test_openai_brain_rejects_invalid_arguments(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[_call("simulate_purchase_preview", "call_bad", "{}")],
                output_parsed=None,
            )
        ],
    )

    with pytest.raises(AIStructuredOutputError):
        OpenAIAgentBrain(_settings()).run_agentic("simulá", [], _ctx())


def test_openai_brain_limits_iterations(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[_call("get_financial_summary", "call_1", "{}")],
                output_parsed=None,
            )
        ],
    )

    with pytest.raises(AIStructuredOutputError):
        OpenAIAgentBrain(_settings(ai_agent_max_iterations=1)).run_agentic("loop", [], _ctx())


def test_openai_brain_timeout(monkeypatch):
    _patch_client(monkeypatch, [FakeTimeout("timeout")])

    with pytest.raises(AIProviderTimeoutError):
        OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())


def test_openai_brain_provider_error(monkeypatch):
    _patch_client(monkeypatch, [FakeAPIError("api")])

    with pytest.raises(AIProviderUnavailableError):
        OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())


def test_openai_brain_classify_keeps_store_false(monkeypatch):
    records = _patch_client(
        monkeypatch, [SimpleNamespace(output=[], output_parsed={"confidence": 0.5})]
    )

    result = OpenAIAgentBrain(_settings()).classify("hola", [])

    assert result["intent"] == AgentIntent.UNKNOWN
    assert records[0]["store"] is False
