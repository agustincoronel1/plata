"""Cerebro del copiloto: clasificación de intención y redacción grounded.

Abstracción de proveedor (igual que el parser): `AgentBrain` es la interfaz; `MockAgentBrain`
es determinístico y sin coste (default en dev/tests/evals); `OpenAIAgentBrain` es el real,
perezoso y desacoplado. El cerebro NO calcula montos: solo clasifica y arma texto a partir de
los resultados de las tools y la evidencia. Los números de la respuesta salen de ahí, no del
modelo.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.ai.agent.schemas import AgentIntent
from app.ai.exceptions import (
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIStructuredOutputError,
)
from app.ai.providers.mock import _normalize

_NUM = r"(\d+(?:[.\s]\d{3})*(?:,\d+)?|\d+)"


def _to_decimal(raw: str) -> Decimal:
    cleaned = raw.replace(" ", "").replace(".", "").replace(",", ".")
    return Decimal(cleaned)


def extract_amount(normalized: str) -> Decimal | None:
    """Extrae un monto en ARS de texto normalizado (lucas=miles, palo=millones, mil)."""
    m = re.search(rf"{_NUM}\s*(lucas?|palos?|mil(?:lones)?|millon)", normalized)
    if m:
        value = _to_decimal(m.group(1))
        unit = m.group(2)
        if unit.startswith("luca"):
            return value * 1000
        if unit.startswith("palo") or unit.startswith("millon") or unit == "millones":
            return value * 1_000_000
        if unit.startswith("mil"):
            return value * 1000
    m = re.search(rf"{_NUM}", normalized)
    return _to_decimal(m.group(1)) if m else None


def extract_installments(normalized: str) -> int | None:
    m = re.search(r"(\d+)\s*cuotas?", normalized)
    return int(m.group(1)) if m else None


def looks_like_money(normalized: str) -> bool:
    return bool(
        re.search(r"\d[\d.\s,]*\s*(lucas?|palos?|mil(?:lones)?|millon)", normalized)
        or re.search(r"\bson\s+\d", normalized)
    )


class AgentBrain(Protocol):
    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]: ...
    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str: ...


class AgentPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AgentIntent = AgentIntent.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    args: dict[str, Any] = Field(default_factory=dict)


class AgentFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=1200)


# Palabras clave por intención (se evalúan en orden de prioridad).
_INJECTION = ("ignora", "borra", "eliminá", "elimina", "instrucciones", "sos un asistente")
_TX_VERBS = ("gaste", "pague", "compre", "cobre", "gane", "transferi", "me entro")


class MockAgentBrain:
    """Clasificador determinístico + redactor grounded, sin coste."""

    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        n = _normalize(message)
        args: dict[str, Any] = {}

        if any(w in n for w in _INJECTION) and not any(v in n for v in _TX_VERBS):
            return {"intent": AgentIntent.UNKNOWN, "confidence": 0.2, "args": args}

        # Compara fechas reusando el contexto de una simulación previa (multi-turn).
        if (
            history
            and ("y si" in n or "empiezo" in n)
            and ("mes" in n or "proximo" in n or "viene" in n)
        ):
            return {"intent": AgentIntent.COMPARE_PURCHASE_DATES, "confidence": 0.7, "args": args}

        installments = extract_installments(n)
        amount = extract_amount(n)
        if "cuotas" in n or "puedo comprar" in n or "simular" in n or "simula" in n:
            args["installments"] = installments
            args["amount"] = str(amount) if amount is not None else None
            intent = AgentIntent.SIMULATE_PURCHASE
            if ("mes" in n and ("viene" in n or "proximo" in n or "empiezo" in n)) and history:
                intent = AgentIntent.COMPARE_PURCHASE_DATES
            return {"intent": intent, "confidence": 0.8, "args": args}

        if any(v in n for v in _TX_VERBS) and extract_amount(n) is not None:
            return {"intent": AgentIntent.CREATE_TRANSACTION, "confidence": 0.75, "args": args}

        if (
            "tengo que pagar" in n
            or "necesito pagar" in n
            or "pagar el" in n
            or "vence" in n
            or "compromiso" in n
            or "alquiler" in n
            or "aumenta" in n
        ):
            commitment_amount = amount if looks_like_money(n) else None
            args["amount"] = str(commitment_amount) if commitment_amount is not None else None
            missing = []
            if commitment_amount is None:
                missing.append("amount")
            if "vence" not in n:
                missing.append("due_date")
            if not any(word in n for word in ("alquiler", "obra social", "colegio", "internet")):
                missing.append("name")
            args["missing_fields"] = missing
            return {"intent": AgentIntent.CREATE_COMMITMENT, "confidence": 0.7, "args": args}

        if "pagos" in n or "compromisos" in n or "vencimientos" in n or "antes de cobrar" in n:
            return {"intent": AgentIntent.LIST_COMMITMENTS, "confidence": 0.8, "args": args}

        if "por que" in n or "explicame" in n or "explica" in n:
            return {"intent": AgentIntent.EXPLAIN_AVAILABLE_MONEY, "confidence": 0.7, "args": args}

        if (
            "cuanto gaste" in n
            or "buscar" in n
            or "parecidos" in n
            or "movimientos" in n
            or "gaste en" in n
            or "gastos" in n
        ):
            args["query"] = message
            return {"intent": AgentIntent.SEARCH_HISTORY, "confidence": 0.7, "args": args}

        if (
            "cuanto puedo gastar" in n
            or "disponible" in n
            or "situacion" in n
            or "resumen" in n
            or "como estoy" in n
        ):
            return {"intent": AgentIntent.DASHBOARD_SUMMARY, "confidence": 0.8, "args": args}

        return {"intent": AgentIntent.UNKNOWN, "confidence": 0.3, "args": args}

    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str:
        from app.ai.agent.answers import render_answer

        return render_answer(intent, context)


class OpenAIAgentBrain:  # pragma: no cover - requiere red y API key
    """Cerebro real. Perezoso: no toca el SDK ni la red sin API key."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._model = settings.ai_model
        self._api_key = settings.ai_api_key
        self._timeout = settings.ai_timeout_seconds
        self._max_retries = settings.ai_max_retries
        self._max_iterations = getattr(settings, "ai_agent_max_iterations", 4)

    def _fail_without_key(self) -> None:
        from app.ai.exceptions import AIProviderUnavailableError

        if not self._settings.ai_api_key:
            raise AIProviderUnavailableError(
                "El copiloto real no está configurado (falta la API key)."
            )

    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        self._fail_without_key()
        completion = self._call_model(
            input=[
                {
                    "role": "system",
                    "content": (
                        "Sos el planificador del copiloto financiero Plata. Elegí solo tools "
                        "del allowlist cuando hagan falta datos o una escritura. Si no hace "
                        "falta tool, devolvé una intención estructurada. No calcules saldos."
                    ),
                },
                *_history_tail(history),
                {"role": "user", "content": message},
            ],
            text_format=AgentPlanOutput,
            tools=_tool_specs(),
            tool_choice="auto",
            store=False,
            metadata={"task": "agent_classify"},
        )
        tool_calls = _extract_tool_calls(completion)
        if tool_calls:
            if len(tool_calls) > self._max_iterations:
                raise AIStructuredOutputError
            return {
                "intent": _intent_from_tool_calls(tool_calls),
                "confidence": 0.85,
                "args": {"_tool_calls": tool_calls},
            }

        parsed = getattr(completion, "output_parsed", None)
        if parsed is None:
            raise AIStructuredOutputError
        try:
            plan = AgentPlanOutput.model_validate(parsed)
        except ValidationError as exc:
            raise AIStructuredOutputError from exc
        return {"intent": plan.intent, "confidence": plan.confidence, "args": plan.args}

    def run_agentic(self, message: str, history: list[dict[str, Any]], ctx: Any) -> dict[str, Any]:
        """Loop real Responses API: function_call -> backend tool -> function_call_output."""
        self._fail_without_key()
        from app.ai.agent.tools import blocked_sensitive_tool_result, is_write_tool, run_tool

        items: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Sos el copiloto financiero Plata. Usá solo tools del allowlist. "
                    "No calcules saldos ni totales: pedí tools. Las escrituras solo "
                    "preparan drafts y requieren aprobación humana."
                ),
            },
            *_history_tail(history),
            {"role": "user", "content": message},
        ]
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        approval = False
        sensitive_write_executed = False

        for _round in range(self._max_iterations):
            completion = self._call_model(
                input=items,
                text_format=AgentFinalAnswer,
                tools=_tool_specs(),
                tool_choice="auto",
                store=False,
                metadata={"task": "agent_chat"},
            )
            calls = _extract_tool_calls(completion, require_call_id=True)
            if not calls:
                parsed = getattr(completion, "output_parsed", None)
                if parsed is None:
                    raise AIStructuredOutputError
                answer = AgentFinalAnswer.model_validate(parsed).answer
                intent = _intent_from_tool_calls(tool_calls) if tool_calls else AgentIntent.UNKNOWN
                return {
                    "intent": intent,
                    "intent_confidence": 0.85 if tool_calls else 0.5,
                    "planner_args": {"_agentic": True},
                    "tool_calls": tool_calls,
                    "tool_results": tool_results,
                    "retrieved_evidence": evidence,
                    "pending_action": pending,
                    "approval_required": approval,
                    "final_answer": answer,
                    "messages": [
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": answer},
                    ],
                    "agentic_done": True,
                }

            output_items = [_safe_response_item(item) for item in getattr(completion, "output", [])]
            items.extend(item for item in output_items if item)
            for call in calls:
                writes = is_write_tool(call["name"])
                if sensitive_write_executed and writes:
                    rec = blocked_sensitive_tool_result(call["name"], call["arguments"])
                else:
                    if writes:
                        sensitive_write_executed = True
                    rec = run_tool(ctx, call["name"], call["arguments"])
                tool_calls.append(
                    {
                        "name": call["name"],
                        "call_id": call["call_id"],
                        "arguments": call["arguments"],
                    }
                )
                tool_results.append(rec)
                if rec["name"] == "search_transactions" and rec["ok"]:
                    evidence = rec["data"]["evidence"]
                if (
                    pending is None
                    and rec.get("writes")
                    and rec["ok"]
                    and rec["data"]
                    and rec["data"].get("is_confirmable")
                ):
                    from uuid import uuid4

                    data = rec["data"]
                    pending = {
                        "action_id": str(uuid4()),
                        "kind": data["kind"],
                        "summary": data["summary"],
                        "draft_id": data["draft_id"],
                        "draft": data.get("fields") or {},
                    }
                    approval = True
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            _safe_tool_output(rec), default=str, ensure_ascii=False
                        ),
                    }
                )

        raise AIStructuredOutputError("La IA necesitó demasiadas rondas para responder.")

    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str:
        self._fail_without_key()
        completion = self._call_model(
            input=[
                {
                    "role": "system",
                    "content": (
                        "Redactá una respuesta breve en español rioplatense usando solo los "
                        "tool_results, evidencia y pending_action provistos. No inventes montos "
                        "ni digas que guardaste una escritura si pending_action sigue pendiente."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "intent": intent.value,
                            "tool_results": context.get("tool_results", []),
                            "evidence": context.get("evidence", []),
                            "pending_action": context.get("pending_action"),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            text_format=AgentFinalAnswer,
            store=False,
            metadata={"task": "agent_answer"},
        )
        parsed = getattr(completion, "output_parsed", None)
        if parsed is None:
            raise AIStructuredOutputError
        try:
            return AgentFinalAnswer.model_validate(parsed).answer
        except ValidationError as exc:
            raise AIStructuredOutputError from exc

    def _call_model(self, **kwargs: Any) -> Any:
        client_cls, api_error, api_timeout = _load_openai_sdk()
        client = client_cls(
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        try:
            started = time.monotonic()
            result = client.responses.parse(model=self._model, **kwargs)
            result._plata_latency_ms = int((time.monotonic() - started) * 1000)
            return result
        except api_timeout as exc:
            raise AIProviderTimeoutError from exc
        except api_error as exc:
            raise AIProviderUnavailableError from exc
        except (AIProviderTimeoutError, AIProviderUnavailableError, AIStructuredOutputError):
            raise
        except Exception as exc:
            raise AIProviderUnavailableError from exc


def _history_tail(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in history[-6:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            safe.append({"role": role, "content": content[:1000]})
    return safe


def _tool_specs() -> list[dict[str, Any]]:
    from app.ai.agent.tools import TOOLS

    specs = []
    for name, tool in TOOLS.items():
        specs.append(
            {
                "type": "function",
                "name": name,
                "description": f"Plata tool autorizada: {name}.",
                "parameters": tool.args_model.model_json_schema(),
                "strict": True,
            }
        )
    return specs


def _extract_tool_calls(completion: Any, *, require_call_id: bool = False) -> list[dict[str, Any]]:
    output = getattr(completion, "output", []) or []
    calls: list[dict[str, Any]] = []
    for item in output:
        kind = _get(item, "type")
        if kind not in ("function_call", "tool_call"):
            continue
        name = _get(item, "name")
        call_id = _get(item, "call_id") or _get(item, "id")
        raw_args = _get(item, "arguments") or {}
        if not isinstance(name, str):
            raise AIStructuredOutputError
        if require_call_id and not isinstance(call_id, str):
            raise AIStructuredOutputError
        call = {"name": name, "arguments": _validate_tool_arguments(name, raw_args)}
        if call_id:
            call["call_id"] = call_id
        calls.append(call)
    return calls


def _validate_tool_arguments(name: str, raw_args: Any) -> dict[str, Any]:
    from app.ai.agent.tools import TOOLS

    tool = TOOLS.get(name)
    if tool is None:
        raise AIStructuredOutputError
    try:
        if isinstance(raw_args, str):
            args = TypeAdapter(tool.args_model).validate_json(raw_args)
        else:
            args = tool.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise AIStructuredOutputError from exc
    return args.model_dump(mode="json", exclude_none=True)


def _intent_from_tool_calls(calls: list[dict[str, Any]]) -> AgentIntent:
    write_names = {c["name"] for c in calls}
    if "create_transaction_draft" in write_names:
        return AgentIntent.CREATE_TRANSACTION
    if "create_commitment_draft" in write_names:
        return AgentIntent.CREATE_COMMITMENT
    if "simulate_purchase_preview" in write_names:
        return AgentIntent.SIMULATE_PURCHASE
    if "search_transactions" in write_names:
        return AgentIntent.SEARCH_HISTORY
    if "list_pending_commitments" in write_names:
        return AgentIntent.LIST_COMMITMENTS
    if "get_financial_summary" in write_names:
        return AgentIntent.DASHBOARD_SUMMARY
    return AgentIntent.UNKNOWN


def _get(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _safe_response_item(item: Any) -> dict[str, Any] | None:
    kind = _get(item, "type")
    if kind not in ("function_call", "tool_call"):
        return None
    name = _get(item, "name")
    call_id = _get(item, "call_id") or _get(item, "id")
    arguments = _get(item, "arguments") or "{}"
    if not isinstance(name, str) or not isinstance(call_id, str):
        raise AIStructuredOutputError
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def _safe_tool_output(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": rec["name"],
        "ok": rec["ok"],
        "error": rec.get("error"),
        "message": rec.get("message"),
        "writes": rec.get("writes", False),
        "data": rec.get("data"),
    }


def _load_openai_sdk() -> tuple[Any, type[Exception], type[Exception]]:
    try:
        from openai import APITimeoutError, OpenAI, OpenAIError
    except ImportError as exc:
        raise AIProviderUnavailableError(
            "El proveedor de IA real no está instalado. Usá AI_PROVIDER=mock."
        ) from exc
    return OpenAI, OpenAIError, APITimeoutError


def build_brain(settings: Any) -> AgentBrain:
    if settings.ai_provider == "openai":
        if settings.ai_model.startswith("mock-"):
            raise AIProviderUnavailableError(
                "AI_MODEL debe ser un modelo real cuando AI_PROVIDER=openai."
            )
        return OpenAIAgentBrain(settings)
    return MockAgentBrain()
