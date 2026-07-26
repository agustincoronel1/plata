"""Nodos del grafo del copiloto. Cada nodo recibe (state, config) y devuelve estado parcial.

Las dependencias no serializables (Session, draft store, gateway, cerebro) viajan por
`config["configurable"]`, NUNCA por el estado (que se persiste en el checkpoint). Así el
estado guardado no contiene secretos ni objetos vivos.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.ai.agent import router, verifier
from app.ai.agent.brain import AgentBrain
from app.ai.agent.schemas import AgentIntent
from app.ai.agent.tools import (
    ToolContext,
    blocked_sensitive_tool_result,
    is_write_tool,
    run_tool,
)


def _ctx(config: dict[str, Any]) -> ToolContext:
    return config["configurable"]["ctx"]


def _brain(config: dict[str, Any]) -> AgentBrain:
    return config["configurable"]["brain"]


def classify_intent(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("pending_commitment_fields"):
        return {
            "intent": AgentIntent.CREATE_COMMITMENT.value,
            "intent_confidence": 0.8,
            "planner_args": {},
            "steps": state.get("steps", 0) + 1,
            "messages": [{"role": "user", "content": state["input"]}],
        }
    brain = _brain(config)
    runner = getattr(brain, "run_agentic", None)
    if callable(runner):
        result = runner(state["input"], state.get("messages", []), _ctx(config))
        result["steps"] = state.get("steps", 0) + 1
        return result
    result = brain.classify(state["input"], state.get("messages", []))
    return {
        "intent": result["intent"].value,
        "intent_confidence": float(result["confidence"]),
        "planner_args": result["args"],
        "steps": state.get("steps", 0) + 1,
        "messages": [{"role": "user", "content": state["input"]}],
    }


def plan_tools(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("agentic_done"):
        return {"tool_calls": state.get("tool_calls", [])}
    ctx = _ctx(config)
    intent = AgentIntent(state["intent"])
    calls = router.plan_tools(
        intent,
        state["input"],
        state.get("planner_args", {}),
        ctx.as_of,
        state.get("last_simulation"),
        state.get("pending_commitment_fields"),
    )
    return {"tool_calls": calls}


def execute_tools(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("agentic_done"):
        return {
            "tool_results": state.get("tool_results", []),
            "retrieved_evidence": state.get("retrieved_evidence", []),
            "pending_action": state.get("pending_action"),
            "approval_required": state.get("approval_required", False),
        }
    ctx = _ctx(config)
    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    approval = False
    last_sim = state.get("last_simulation")
    sensitive_write_executed = False

    for call in state.get("tool_calls", []):
        writes = is_write_tool(call["name"])
        if sensitive_write_executed and writes:
            rec = blocked_sensitive_tool_result(call["name"], call["arguments"])
        else:
            if writes:
                sensitive_write_executed = True
            rec = run_tool(ctx, call["name"], call["arguments"])
        results.append(rec)
        if rec["name"] == "search_transactions" and rec["ok"]:
            evidence = rec["data"]["evidence"]
        if rec["name"] == "simulate_purchase_preview" and rec["ok"]:
            last_sim = {
                "amount": call["arguments"]["total_amount"],
                "installments": call["arguments"]["installments"],
            }
        if (
            pending is None
            and rec.get("writes")
            and rec["ok"]
            and rec["data"]
            and rec["data"].get("is_confirmable")
        ):
            data = rec["data"]
            pending = {
                "action_id": str(uuid4()),
                "kind": data["kind"],
                "summary": data["summary"],
                "draft_id": data["draft_id"],
                "draft": data.get("fields") or {},
            }
            approval = True

    partial: dict[str, Any] = {
        "tool_results": results,
        "retrieved_evidence": evidence,
        "pending_action": pending,
        "approval_required": approval,
        "pending_commitment_fields": state.get("pending_commitment_fields"),
    }
    if state["intent"] == AgentIntent.CREATE_COMMITMENT.value:
        partial["pending_commitment_fields"] = router.commitment_fields_after_turn(
            state["input"],
            state.get("planner_args", {}),
            ctx.as_of,
            state.get("pending_commitment_fields"),
            completed=bool(pending),
        )
    if last_sim:
        partial["last_simulation"] = last_sim
    return partial


def generate_answer(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("agentic_done"):
        return {"final_answer": state.get("final_answer", ""), "messages": []}
    brain = _brain(config)
    intent = AgentIntent(state["intent"])
    context = {
        "tool_results": state.get("tool_results", []),
        "evidence": state.get("retrieved_evidence", []),
        "pending_action": state.get("pending_action"),
        "planner_args": state.get("planner_args", {}),
        "pending_commitment_fields": state.get("pending_commitment_fields"),
    }
    answer = brain.answer(intent, context)
    return {"final_answer": answer, "messages": [{"role": "assistant", "content": answer}]}


def verify_results(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    ok, reasons = verifier.verify(
        answer=state.get("final_answer", ""),
        tool_results=state.get("tool_results", []),
        evidence=state.get("retrieved_evidence", []),
        pending_action=state.get("pending_action"),
        approval_required=state.get("approval_required", False),
    )
    if ok:
        return {"verifier_ok": True}
    safe = "No pude verificar la respuesta, así que no la muestro. Probá el formulario manual."
    return {
        "verifier_ok": False,
        "errors": reasons,
        "final_answer": safe,
        "pending_action": None,
        "approval_required": False,
        "pending_commitment_fields": None,
    }


def apply_write(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Se ejecuta SOLO tras aprobación (reanudación desde el checkpoint)."""
    ctx = _ctx(config)
    pending = state.get("pending_action")
    if not pending:
        return {"approval_required": False}

    from app.services.ai_chat_service import apply_pending_action

    if state.get("approved"):
        answer = apply_pending_action(ctx, pending)
    else:
        apply_pending_action(ctx, pending, reject=True)
        answer = "Listo, no registré nada. El movimiento quedó descartado."

    return {
        "final_answer": answer,
        "approval_required": False,
        "pending_action": None,
        "pending_commitment_fields": None,
        "messages": [{"role": "assistant", "content": answer}],
    }


def route_after_verify(state: dict[str, Any]) -> str:
    return "apply_write" if state.get("approval_required") else "__end__"
