"""Orquestación del copiloto: corre el grafo, pausa en escrituras y reanuda al aprobar.

Independiente de FastAPI. El estado conversacional vive en el checkpointer del grafo (por
`conversation_id`). La acción pendiente se recupera SIEMPRE del estado persistido, nunca de
argumentos reenviados por el frontend.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.ai.agent.brain import build_brain
from app.ai.agent.graph import RECURSION_LIMIT, get_compiled_graph
from app.ai.agent.schemas import (
    AgentEvidence,
    AgentIntent,
    ChatResponse,
    ConversationMessage,
    ConversationResponse,
    PendingAction,
    ToolCallTrace,
)
from app.ai.agent.tools import ToolContext
from app.ai.exceptions import AIError, DraftNotFoundError
from app.ai.gateway import get_ai_gateway
from app.core.config import settings
from app.core.constants import DEMO_USER_ID
from app.schemas.ai_transaction import TransactionConfirmRequest
from app.schemas.commitment import CommitmentCreate
from app.services import ai_transaction_service, commitment_service
from app.services.draft_store import DraftStatus, DraftStore, get_draft_store


class PendingActionNotFoundError(AIError):
    status_code = 404
    default_detail = "No hay una acción pendiente de aprobación en esta conversación."


class PendingActionMismatchError(AIError):
    status_code = 409
    default_detail = "La acción a aprobar no coincide con la pendiente."


class PendingActionAwaitingResolutionError(AIError):
    status_code = 409
    default_detail = (
        "Primero tenes que aprobar o rechazar la accion pendiente antes de enviar otro mensaje."
    )


def _context(
    session: Session, draft_store: DraftStore | None, gateway: Any, as_of: date | None
) -> ToolContext:
    return ToolContext(
        session=session,
        draft_store=draft_store or get_draft_store(),
        gateway=gateway or get_ai_gateway(),
        as_of=as_of or date.today(),
    )


def _config(conversation_id: uuid.UUID, ctx: ToolContext, brain: Any) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": str(conversation_id), "ctx": ctx, "brain": brain},
        "recursion_limit": RECURSION_LIMIT,
    }


def chat(
    session: Session,
    message: str,
    conversation_id: uuid.UUID | None = None,
    *,
    as_of: date | None = None,
    draft_store: DraftStore | None = None,
    gateway: Any = None,
    brain: Any = None,
) -> ChatResponse:
    conversation_id = conversation_id or uuid.uuid4()
    ctx = _context(session, draft_store, gateway, as_of)
    brain = brain or build_brain(settings)
    trace_id = uuid.uuid4().hex

    graph = get_compiled_graph()
    config = _config(conversation_id, ctx, brain)
    snapshot = graph.get_state(config)
    if _active_pending_action(snapshot):
        raise PendingActionAwaitingResolutionError

    result = graph.invoke(
        {
            "input": message,
            "conversation_id": str(conversation_id),
            "user_id": str(DEMO_USER_ID),
            "trace_id": trace_id,
            "approved": None,
            "agentic_done": False,
            "tool_calls": [],
            "tool_results": [],
            "retrieved_evidence": [],
            "pending_action": None,
            "approval_required": False,
            "final_answer": "",
        },
        config,
    )
    _trace(conversation_id, trace_id, result, message)
    return _response(conversation_id, trace_id, result)


def resume(
    session: Session,
    conversation_id: uuid.UUID,
    action_id: uuid.UUID,
    *,
    approve: bool,
    as_of: date | None = None,
    draft_store: DraftStore | None = None,
    gateway: Any = None,
    brain: Any = None,
) -> ChatResponse:
    ctx = _context(session, draft_store, gateway, as_of)
    brain = brain or build_brain(settings)
    graph = get_compiled_graph()
    config = _config(conversation_id, ctx, brain)

    snapshot = graph.get_state(config)
    pending = snapshot.values.get("pending_action") if snapshot else None
    if not pending or "apply_write" not in getattr(snapshot, "next", ()):  # no pausa activa
        raise PendingActionNotFoundError
    if str(pending.get("action_id")) != str(action_id):
        raise PendingActionMismatchError

    graph.update_state(config, {"approved": approve})
    result = graph.invoke(None, config)
    trace_id = result.get("trace_id", uuid.uuid4().hex)
    return _response(conversation_id, trace_id, result)


def _active_pending_action(snapshot: Any) -> dict[str, Any] | None:
    pending = snapshot.values.get("pending_action") if snapshot else None
    if pending and "apply_write" in getattr(snapshot, "next", ()):
        return pending
    return None


def get_conversation(conversation_id: uuid.UUID) -> ConversationResponse:
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": str(conversation_id)}}
    snapshot = graph.get_state(config)
    values = snapshot.values if snapshot else {}
    messages = [
        ConversationMessage(role=m["role"], content=m["content"])
        for m in values.get("messages", [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    return ConversationResponse(conversation_id=conversation_id, messages=messages)


def apply_pending_action(ctx: ToolContext, pending: dict[str, Any], *, reject: bool = False) -> str:
    """Ejecuta (o rechaza) la escritura pendiente. La llama el nodo apply_write del grafo."""
    draft_id = uuid.UUID(pending["draft_id"])
    kind = pending["kind"]

    if reject:
        try:
            ctx.draft_store.mark_rejected(draft_id)
        except DraftNotFoundError:
            pass
        return "Descartado."

    if kind == "create_transaction":
        result = ai_transaction_service.confirm_transaction(
            ctx.session, ctx.draft_store, draft_id, TransactionConfirmRequest(confirmed=True)
        )
        tx = result.transaction
        kind_word = "ingreso" if tx.type.value == "income" else "gasto"
        return f"Registré el {kind_word} de {_fmt_money(tx.amount)} en {tx.category}."

    if kind == "create_commitment":
        return _confirm_commitment(ctx, draft_id)

    return "No pude aplicar la acción."


def _confirm_commitment(ctx: ToolContext, draft_id: uuid.UUID) -> str:
    tx_store, same_transaction = _bind_store_to_session(ctx.draft_store, ctx.session)
    draft = tx_store.claim_for_confirmation(draft_id)
    try:
        fields = draft.payload["fields"]
        payload = CommitmentCreate.model_validate(fields)
        if same_transaction:
            commitment = commitment_service.create_commitment_no_commit(ctx.session, payload)
            tx_store.mark_confirmed(draft_id)
            ctx.session.commit()
            ctx.session.refresh(commitment)
        else:
            commitment = commitment_service.create_commitment(ctx.session, payload)
            tx_store.mark_confirmed(draft_id)
    except Exception:
        ctx.session.rollback()
        if not same_transaction:
            tx_store.release_to_pending(draft_id)
        raise
    return f"Agendé el compromiso {commitment.name} de {_fmt_money(commitment.amount)}."


def _bind_store_to_session(store: DraftStore, session: Session) -> tuple[DraftStore, bool]:
    binder = getattr(store, "with_session", None)
    if callable(binder):
        return binder(session), True
    return store, False


def _fmt_money(value: Any) -> str:
    from decimal import Decimal

    return "$" + f"{Decimal(str(value)):,.0f}".replace(",", ".")


def _trace(conversation_id: uuid.UUID, trace_id: str, state: dict[str, Any], message: str) -> None:
    from app.ai.trace import log_chat_trace

    results = state.get("tool_results", [])
    log_chat_trace(
        trace_id=trace_id,
        conversation_id=str(conversation_id),
        intent=state.get("intent", "unknown"),
        tools_used=[r["name"] for r in results],
        tool_durations_ms=[r.get("duration_ms", 0) for r in results],
        evidence_count=len(state.get("retrieved_evidence", [])),
        approval_required=bool(state.get("approval_required")),
        verifier_ok=bool(state.get("verifier_ok", True)),
        steps=int(state.get("steps", 0)),
        message=message,
    )


def _response(conversation_id: uuid.UUID, trace_id: str, state: dict[str, Any]) -> ChatResponse:
    intent = AgentIntent(state.get("intent", "unknown"))
    tools = [
        ToolCallTrace(
            name=r["name"], arguments=r["arguments"], ok=r["ok"], duration_ms=r["duration_ms"]
        )
        for r in state.get("tool_results", [])
    ]
    evidence = [AgentEvidence.model_validate(e) for e in state.get("retrieved_evidence", [])]
    pending_raw = state.get("pending_action")
    pending = (
        PendingAction(
            action_id=pending_raw["action_id"],
            kind=pending_raw["kind"],
            summary=pending_raw["summary"],
            draft=pending_raw.get("draft") or {},
        )
        if pending_raw
        else None
    )
    return ChatResponse(
        conversation_id=conversation_id,
        message_id=uuid.uuid4(),
        answer=state.get("final_answer", ""),
        intent=intent,
        tools_used=tools,
        evidence=evidence,
        assumptions=state.get("assumptions", []),
        requires_approval=bool(state.get("approval_required")),
        pending_action=pending,
        trace_id=trace_id,
    )


# El status DraftStatus se importa para exponerlo a quien construya respuestas de estado.
__all__ = ["chat", "resume", "get_conversation", "apply_pending_action", "DraftStatus"]
