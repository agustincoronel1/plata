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
    # Cómo se resuelve el turno (ver AgentRoute): datos, simulación, escritura, aclaración,
    # charla, mixto o error. Es lo que decide qué se le exige a la respuesta.
    route: str
    # Argumentos extraídos por el clasificador y contexto de la última simulación (multi-turn).
    planner_args: dict[str, Any]
    last_simulation: dict[str, Any] | None
    # Lo que quedó a medias: intención, datos ya conocidos y datos que faltan. Es lo que
    # permite que "1.200.000" complete la simulación de la notebook dos turnos después.
    pending_request: dict[str, Any] | None
    # Última consulta de datos resuelta, para entender "¿y el mes pasado?".
    last_query: dict[str, Any] | None
    # Última respuesta estructurada mostrada, para poder explicarla si preguntan por qué.
    last_structured_answer: dict[str, Any] | None
    # Los montos de ESA última respuesta, y nada más. Sirven para que un "¿por qué?" pueda
    # repetir la cifra que se acaba de dar sin que el verificador la trate como inventada.
    #
    # Deliberadamente NO se acumulan a lo largo de la conversación: una allowlist con todo
    # lo dicho en el hilo terminaría avalando una afirmación nueva porque el número aparece
    # en algo que se respondió veinte turnos atrás y no tiene nada que ver. El reducer por
    # defecto (sobreescribir) es exactamente lo que se quiere.
    last_answer_amounts: list[int]
    agentic_done: bool
    # Campos por-turno: el reducer por defecto los sobreescribe en cada turno.
    missing_fields: list[str]
    answer_kind: str
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
