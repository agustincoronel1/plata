"""Orquestación del copiloto: corre el grafo, pausa en escrituras y reanuda al aprobar.

Independiente de FastAPI. El estado conversacional vive en el checkpointer del grafo. La
acción pendiente se recupera SIEMPRE del estado persistido, nunca de argumentos reenviados
por el frontend.

Aislamiento entre cuentas: el `thread_id` del checkpointer NO es el `conversation_id` a
secas, sino `<user_id>:<conversation_id>`. Esa es la pieza clave — el `conversation_id`
viaja por la URL, así que si fuera el thread completo alcanzaría con conocerlo para leer o
reanudar la conversación de otra persona. Con el usuario adentro, un id ajeno resuelve un
hilo distinto (vacío) y no hay nada que filtrar: no hace falta tabla ni migración.

`user_id` es keyword-only y obligatorio en las tres entradas públicas. Sale del JWT
verificado y viaja a las tools por `config["configurable"]`, fuera del estado y fuera del
prompt: el modelo no lo ve ni puede cambiarlo.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
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
    StructuredAnswer,
    ToolCallTrace,
)
from app.ai.agent.tools import ToolContext
from app.ai.exceptions import AIError, DraftNotFoundError
from app.ai.fast_path import match_fast_path
from app.ai.gateway import get_ai_gateway
from app.ai.trace import log_fast_path_hit, log_fast_path_miss
from app.core.config import settings
from app.core.timezone import app_today
from app.schemas.ai_transaction import TransactionConfirmRequest
from app.schemas.commitment import CommitmentCreate
from app.services import ai_transaction_service, commitment_service, fast_path_service
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
    session: Session,
    user_id: uuid.UUID,
    draft_store: DraftStore | None,
    gateway: Any,
    as_of: date | None,
) -> ToolContext:
    return ToolContext(
        session=session,
        draft_store=draft_store or get_draft_store(),
        gateway=gateway or get_ai_gateway(),
        # `app_today()` y no `date.today()`: el "hoy" del copiloto tiene que ser el mismo
        # que el del resto de la aplicación (la zona de negocio, `APP_TIMEZONE`). Render
        # corre en UTC, así que con `date.today()` un mensaje de las 22:00 de Argentina se
        # fechaba con el día siguiente: "agendá esto para mañana" caía dos días después, y
        # un gasto registrado por chat quedaba en otro día que el mismo gasto cargado a mano.
        as_of=as_of or app_today(),
        user_id=user_id,
    )


def _thread_id(user_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """Hilo del checkpointer, siempre acotado al dueño.

    El `conversation_id` de otra persona resuelve un hilo distinto —vacío— en lugar de su
    conversación. No hay 403 que revele que existe: sencillamente no hay nada ahí.
    """
    return f"{user_id}:{conversation_id}"


def _config(conversation_id: uuid.UUID, ctx: ToolContext, brain: Any) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": _thread_id(ctx.user_id, conversation_id),
            "ctx": ctx,
            "brain": brain,
        },
        "recursion_limit": RECURSION_LIMIT,
    }


def chat(
    session: Session,
    message: str,
    conversation_id: uuid.UUID | None = None,
    *,
    user_id: uuid.UUID,
    as_of: date | None = None,
    draft_store: DraftStore | None = None,
    gateway: Any = None,
    brain: Any = None,
    before_provider: Callable[[], None] | None = None,
) -> ChatResponse:
    """Corre un turno del copiloto.

    `before_provider` es un gancho que se ejecuta en el único punto donde ya se sabe que
    esta petición va a invocar al modelo: después del chequeo de acción pendiente y antes
    de arrancar el grafo. La API lo usa para reservar cuota diaria ahí y no antes, porque
    un 409 por acción pendiente no gasta ninguna llamada. Quien llama al servicio sin
    pasarlo —evaluadores offline, smoke real— corre sin límites, que es lo correcto.
    """
    conversation_id = conversation_id or uuid.uuid4()
    ctx = _context(session, user_id, draft_store, gateway, as_of)
    brain = brain or build_brain(settings)
    trace_id = uuid.uuid4().hex

    graph = get_compiled_graph()
    config = _config(conversation_id, ctx, brain)
    snapshot = graph.get_state(config)
    if _active_pending_action(snapshot):
        raise PendingActionAwaitingResolutionError

    # Atajo determinístico. Va acá y no antes por dos razones: el 409 por acción pendiente
    # tiene que seguir ganando (una escritura sin resolver bloquea el turno, como siempre),
    # y esta es la última línea antes de que el turno empiece a costar plata. Si resuelve,
    # se sale sin invocar `before_provider`, así que no se reserva cuota, y sin llamar a
    # `graph.invoke`, así que no corre el grafo, ni las tools, ni el RAG, ni el modelo.
    fast_answer = _try_fast_path(
        session, message, conversation_id, snapshot, user_id=user_id, as_of=as_of
    )
    if fast_answer is not None:
        return fast_answer

    # Desde acá sí o sí se llama al modelo: es el punto exacto donde corresponde cobrar.
    if before_provider is not None:
        before_provider()

    result = graph.invoke(
        {
            "input": message,
            "conversation_id": str(conversation_id),
            # Informativo para las trazas. El dueño autoritativo es `ctx.user_id`:
            # el estado se persiste en el checkpoint y no es una fuente confiable.
            "user_id": str(user_id),
            "trace_id": trace_id,
            "approved": None,
            "agentic_done": False,
            "tool_calls": [],
            "tool_results": [],
            "retrieved_evidence": [],
            "pending_action": None,
            "approval_required": False,
            "final_answer": "",
            "structured_answer": None,
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
    user_id: uuid.UUID,
    approve: bool,
    as_of: date | None = None,
    draft_store: DraftStore | None = None,
    gateway: Any = None,
    brain: Any = None,
) -> ChatResponse:
    ctx = _context(session, user_id, draft_store, gateway, as_of)
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


def _try_fast_path(
    session: Session,
    message: str,
    conversation_id: uuid.UUID,
    snapshot: Any,
    *,
    user_id: uuid.UUID,
    as_of: date | None,
) -> ChatResponse | None:
    """Resuelve el turno sin IA si la consulta es simple. `None` = seguí con el agente.

    Tres puertas, y basta con que una se cierre para que el mensaje siga de largo: que el
    clasificador reconozca la frase, que la conversación no esté en medio de un alta a
    medias, y que el servicio encuentre los datos. Ninguna de las tres consulta al modelo.

    `as_of` es el de `chat()`, no el del ToolContext: el fast path fecha con la zona de
    negocio (`app_today()`), que es la que decide a qué día pertenece un movimiento.
    """
    match = match_fast_path(message)
    if match is None or _in_multi_turn_write(snapshot):
        log_fast_path_miss()
        return None

    answer = fast_path_service.execute_fast_path(
        session, match, user_id=user_id, conversation_id=conversation_id, as_of=as_of
    )
    if answer is None:
        log_fast_path_miss()
        return None

    log_fast_path_hit(
        intent=match.intent.value, period=match.period.value if match.period else None
    )
    return answer


def _in_multi_turn_write(snapshot: Any) -> bool:
    """True si la conversación está completando un compromiso a lo largo de varios turnos.

    Con un alta a medias, el grafo interpreta el mensaje siguiente como la respuesta al
    dato que falta ("son 350 mil"). Contestar ahí una consulta de saldo sería cambiar el
    comportamiento actual del copiloto, así que el atajo se aparta y deja seguir al agente.
    """
    values = snapshot.values if snapshot else {}
    return bool(values.get("pending_commitment_fields"))


def _active_pending_action(snapshot: Any) -> dict[str, Any] | None:
    pending = snapshot.values.get("pending_action") if snapshot else None
    if pending and "apply_write" in getattr(snapshot, "next", ()):
        return pending
    return None


def get_conversation(conversation_id: uuid.UUID, *, user_id: uuid.UUID) -> ConversationResponse:
    """Historial de una conversación propia.

    Una conversación ajena resuelve un hilo vacío: se responde con la lista de mensajes
    vacía, sin confirmar que ese `conversation_id` exista para otra persona.
    """
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": _thread_id(user_id, conversation_id)}}
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
            ctx.draft_store.mark_rejected(draft_id, user_id=ctx.user_id)
        except DraftNotFoundError:
            pass
        return "Descartado."

    if kind == "create_transaction":
        result = ai_transaction_service.confirm_transaction(
            ctx.session,
            ctx.draft_store,
            draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=ctx.user_id,
        )
        tx = result.transaction
        kind_word = "ingreso" if tx.type.value == "income" else "gasto"
        return f"Registré el {kind_word} de {_fmt_money(tx.amount)} en {tx.category}."

    if kind == "create_commitment":
        return _confirm_commitment(ctx, draft_id)

    return "No pude aplicar la acción."


def _confirm_commitment(ctx: ToolContext, draft_id: uuid.UUID) -> str:
    tx_store, same_transaction = _bind_store_to_session(ctx.draft_store, ctx.session)
    draft = tx_store.claim_for_confirmation(draft_id, user_id=ctx.user_id)
    try:
        fields = draft.payload["fields"]
        payload = CommitmentCreate.model_validate(fields)
        if same_transaction:
            commitment = commitment_service.create_commitment_no_commit(
                ctx.session, ctx.user_id, payload
            )
            tx_store.mark_confirmed(draft_id, user_id=ctx.user_id)
            ctx.session.commit()
            ctx.session.refresh(commitment)
        else:
            commitment = commitment_service.create_commitment(ctx.session, ctx.user_id, payload)
            tx_store.mark_confirmed(draft_id, user_id=ctx.user_id)
    except Exception:
        ctx.session.rollback()
        if not same_transaction:
            tx_store.release_to_pending(draft_id, user_id=ctx.user_id)
        raise
    # La confirmación repite los datos guardados, no un "listo" a secas: es la única forma
    # de que la persona detecte al toque que la fecha o la categoría salieron mal.
    confirmacion = (
        f"Agendé {commitment.name} de {_fmt_money(commitment.amount)} "
        f"en {commitment.category}, para el {commitment.due_date.isoformat()}"
    )
    if commitment.is_recurring:
        confirmacion += ", todos los meses"
    return confirmacion + "."


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
    structured_raw = state.get("structured_answer")
    return ChatResponse(
        conversation_id=conversation_id,
        message_id=uuid.uuid4(),
        answer=state.get("final_answer", ""),
        structured_answer=(
            StructuredAnswer.model_validate(structured_raw) if structured_raw else None
        ),
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
