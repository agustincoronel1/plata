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
from tests.conftest import TEST_USER_ID


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


def _reasoning(item_id: str, encrypted: str = "gAAAAA-opaco"):
    """Item de razonamiento como lo devuelve un modelo de razonamiento con store=False."""
    return SimpleNamespace(
        type="reasoning",
        id=item_id,
        summary=[],
        encrypted_content=encrypted,
    )


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
        user_id=TEST_USER_ID,
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
        ctx.draft_store.get(UUID(result["pending_action"]["draft_id"]), user_id=ctx.user_id).status
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


# --- Razonamiento: se reenvía entre rondas, nunca se expone --------------------------


def test_reasoning_se_reenvia_en_la_ronda_siguiente(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _reasoning("rs_1"),
                    _call("get_financial_summary", "call_1", "{}"),
                ],
                output_parsed=None,
            ),
            _final("Resumen listo."),
        ],
    )

    OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())

    second_input = records[1]["input"]
    reasoning_items = [item for item in second_input if item.get("type") == "reasoning"]
    assert len(reasoning_items) == 1
    assert reasoning_items[0]["id"] == "rs_1"
    assert reasoning_items[0]["encrypted_content"] == "gAAAAA-opaco"
    # El razonamiento va ANTES de su function_call, como lo devolvió el modelo.
    kinds = [item.get("type") for item in second_input]
    assert kinds.index("reasoning") < kinds.index("function_call")
    assert kinds.index("function_call") < kinds.index("function_call_output")


def test_reasoning_se_acumula_en_rondas_sucesivas(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[_reasoning("rs_1"), _call("get_financial_summary", "call_1", "{}")],
                output_parsed=None,
            ),
            SimpleNamespace(
                output=[_reasoning("rs_2"), _call("list_pending_commitments", "call_2", "{}")],
                output_parsed=None,
            ),
            _final("Listo."),
        ],
    )

    OpenAIAgentBrain(_settings()).run_agentic("multi step", [], _ctx())

    ids = [item["id"] for item in records[2]["input"] if item.get("type") == "reasoning"]
    assert ids == ["rs_1", "rs_2"]


def test_reasoning_pide_el_contenido_cifrado(monkeypatch):
    records = _patch_client(monkeypatch, [_final("Sin tools.")])

    OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())

    assert records[0]["include"] == ["reasoning.encrypted_content"]
    assert records[0]["store"] is False


def test_reasoning_no_se_expone_al_usuario_ni_al_estado(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    _reasoning("rs_1", encrypted="secreto-de-razonamiento"),
                    _call("get_financial_summary", "call_1", "{}"),
                ],
                output_parsed=None,
            ),
            _final("Resumen listo."),
        ],
    )

    result = OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())

    dumped = repr(result)
    assert "secreto-de-razonamiento" not in dumped
    assert "rs_1" not in dumped
    # El razonamiento no es una tool call ni un mensaje de la conversación.
    assert [call["name"] for call in result["tool_calls"]] == ["get_financial_summary"]
    assert all(message["role"] in ("user", "assistant") for message in result["messages"])


def test_reasoning_sin_id_es_error_seguro(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    SimpleNamespace(type="reasoning", id=None, summary=[], encrypted_content="x"),
                    _call("get_financial_summary", "call_1", "{}"),
                ],
                output_parsed=None,
            )
        ],
    )

    with pytest.raises(AIStructuredOutputError):
        OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())


def test_items_desconocidos_se_siguen_descartando(monkeypatch):
    records = _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[
                    SimpleNamespace(type="message", id="msg_1", content="ruido"),
                    _call("get_financial_summary", "call_1", "{}"),
                ],
                output_parsed=None,
            ),
            _final("Listo."),
        ],
    )

    OpenAIAgentBrain(_settings()).run_agentic("resumen", [], _ctx())

    assert not any(item.get("type") == "message" for item in records[1]["input"])


def _strict_violations(schema, path="#"):
    """Reglas de `strict` de la Responses API: objetos cerrados y `required` completo."""
    out = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            props = set(schema.get("properties", {}) or {})
            required = set(schema.get("required", []) or [])
            if props - required:
                out.append(f"{path}: required incompleto {sorted(props - required)}")
            if schema.get("additionalProperties") is not False:
                out.append(f"{path}: additionalProperties abierto")
        for key, value in schema.items():
            if isinstance(value, (dict, list)):
                out += _strict_violations(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            out += _strict_violations(value, f"{path}[{index}]")
    return out


def test_tool_specs_cumplen_el_contrato_strict_de_la_api():
    # Regresión: el schema crudo de Pydantic deja fuera de `required` los campos con
    # default, y la API responde 400 antes de ejecutar ninguna tool.
    violations = {
        spec["name"]: _strict_violations(spec["parameters"])
        for spec in _tool_specs()
        if _strict_violations(spec["parameters"])
    }
    assert violations == {}


def _free_form_objects(schema, path="#"):
    """Objetos sin `properties` declaradas: `strict` no los admite y el SDK no los corrige.

    En `text_format` el SDK completa `required` y cierra los objetos que tienen
    `properties`, pero un `dict[str, Any]` queda como objeto libre y la API lo rechaza.
    """
    out = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" and not schema.get("properties"):
            out.append(path)
        for key, value in schema.items():
            if isinstance(value, (dict, list)):
                out += _free_form_objects(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            out += _free_form_objects(value, f"{path}[{index}]")
    return out


def test_plan_output_no_expone_objetos_libres():
    from app.ai.agent.brain import AgentPlanOutput

    assert _free_form_objects(AgentPlanOutput.model_json_schema()) == []


def test_plan_output_conserva_las_pistas_del_planner(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            SimpleNamespace(
                output=[],
                output_parsed={
                    "intent": "simulate_purchase",
                    "confidence": 0.8,
                    "args": {"amount": "250000", "installments": 6, "query": None},
                },
            )
        ],
    )

    result = OpenAIAgentBrain(_settings()).classify("simulá una compra", [])

    assert result["intent"] == AgentIntent.SIMULATE_PURCHASE
    assert result["args"] == {"amount": "250000", "installments": 6}


def _null_defaults(schema, path="#"):
    """`default: null` no está permitido en strict: el null ya viaja en el `anyOf`."""
    out = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "default" and value is None:
                out.append(path)
            elif isinstance(value, (dict, list)):
                out += _null_defaults(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            out += _null_defaults(value, f"{path}[{index}]")
    return out


def test_ningun_schema_enviado_conserva_defaults_none():
    from app.ai.agent.tools import TOOLS

    # El schema crudo de Pydantic sí los tiene: por eso hace falta transformarlo.
    crudos = [
        name for name, tool in TOOLS.items() if _null_defaults(tool.args_model.model_json_schema())
    ]
    assert crudos, "el test perdió sentido: Pydantic ya no emite default null"

    enviados = {
        spec["name"]: _null_defaults(spec["parameters"])
        for spec in _tool_specs()
        if _null_defaults(spec["parameters"])
    }
    assert enviados == {}


def test_transformacion_local_equivale_a_la_del_sdk():
    from app.ai.agent.brain import (
        _drop_null_defaults,
        _local_strict_schema,
        _sdk_strict_schema,
    )
    from app.ai.agent.tools import TOOLS

    for name, tool in TOOLS.items():
        del name
        sdk = _sdk_strict_schema(tool.args_model)
        assert sdk is not None, "el SDK instalado debería ofrecer la transformación oficial"
        local = _local_strict_schema(tool.args_model.model_json_schema())
        assert _drop_null_defaults(sdk) == _drop_null_defaults(local)


def test_transformacion_local_cierra_defs_refs_y_arrays():
    from app.ai.agent.brain import _local_strict_schema

    schema = {
        "$defs": {
            "Nested": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                "required": ["a"],
            }
        },
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Nested"}},
            "either": {"anyOf": [{"$ref": "#/$defs/Nested"}, {"type": "null"}]},
            "both": {"allOf": [{"$ref": "#/$defs/Nested"}]},
        },
        "required": ["items"],
    }

    strict = _local_strict_schema(schema)

    nested = strict["$defs"]["Nested"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["a", "b"]
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["items", "either", "both"]
    # Los $ref se conservan intactos: strict los admite si el $defs está normalizado.
    assert strict["properties"]["items"]["items"] == {"$ref": "#/$defs/Nested"}
    assert strict["properties"]["either"]["anyOf"][0] == {"$ref": "#/$defs/Nested"}


def test_tool_specs_no_pierden_propiedades_ni_validacion():
    from app.ai.agent.tools import TOOLS

    specs = {spec["name"]: spec for spec in _tool_specs()}
    assert set(specs) == set(TOOLS)
    search = specs["search_transactions"]["parameters"]
    assert set(search["properties"]) == set(
        TOOLS["search_transactions"].args_model.model_json_schema()["properties"]
    )
    # Los opcionales viajan como null y Pydantic los sigue descartando al ejecutar.
    from app.ai.agent.brain import _validate_tool_arguments

    args = _validate_tool_arguments(
        "search_transactions",
        '{"query":"nafta","category":null,"tx_type":null,"date_from":null,'
        '"date_to":null,"top_k":5}',
    )
    assert args == {"query": "nafta", "top_k": 5}


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
