"""Estado del grafo del copiloto (LangGraph)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    trace_id: str
    # Historial multi-turn: se acumula entre turnos (persistido por el checkpointer).
    messages: Annotated[list[dict[str, Any]], operator.add]
    # Entrada del turno actual.
    input: str
    intent: str
    intent_confidence: float
    # Argumentos extraídos por el clasificador y contexto de la última simulación (multi-turn).
    planner_args: dict[str, Any]
    last_simulation: dict[str, Any] | None
    pending_commitment_fields: dict[str, Any] | None
    agentic_done: bool
    # Campos por-turno: el reducer por defecto los sobreescribe en cada turno.
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    retrieved_evidence: list[dict[str, Any]]
    assumptions: list[str]
    pending_action: dict[str, Any] | None
    approval_required: bool
    approved: bool | None
    verifier_ok: bool
    errors: list[str]
    final_answer: str
    # Respuesta armada campo por campo por la capa de presentación (ver StructuredAnswer).
    structured_answer: dict[str, Any] | None
    steps: int
