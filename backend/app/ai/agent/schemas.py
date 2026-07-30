"""Contrato del copiloto: intents, evidencia y request/response del chat."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ai_usage import AIUsageMetadata


class AgentIntent(StrEnum):
    DASHBOARD_SUMMARY = "dashboard_summary"
    EXPLAIN_AVAILABLE_MONEY = "explain_available_money"
    DAILY_BUDGET = "daily_budget"
    LIST_COMMITMENTS = "list_commitments"
    SEARCH_HISTORY = "search_history"
    # Compra al contado (pago único hoy) vs. compra financiada en cuotas: son caminos
    # distintos a propósito. Una consulta al contado nunca debe pasar por el simulador.
    ONE_TIME_PURCHASE = "one_time_purchase"
    SIMULATE_PURCHASE = "simulate_purchase"
    COMPARE_PURCHASE_DATES = "compare_purchase_dates"
    CREATE_TRANSACTION = "create_transaction"
    CREATE_COMMITMENT = "create_commitment"
    UNKNOWN = "unknown"


RetrievalMethod = Literal["sql", "full_text", "vector", "hybrid"]


class AgentEvidence(BaseModel):
    """Un movimiento (u objeto) que respalda una afirmación de la respuesta."""

    evidence_id: str
    source_type: str
    source_id: str
    title: str
    excerpt: str
    occurred_on: date | None = None
    amount: Decimal | None = None
    retrieval_method: RetrievalMethod
    score: float


class ToolCallTrace(BaseModel):
    """Qué herramienta se usó, sin exponer SQL ni internals."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int


class PendingAction(BaseModel):
    """Escritura propuesta que espera aprobación humana. Se recupera del estado, no del front."""

    action_id: UUID
    kind: Literal["create_transaction", "create_commitment"]
    summary: str
    draft: dict[str, Any]


# Qué decide la respuesta, en términos del usuario (nunca "is_viable" ni "conclusion").
AnswerVerdict = Literal["yes", "no", "info", "needs_input", "unavailable"]


class AnswerDetail(BaseModel):
    """Una línea de detalle ya formateada para mostrar (p. ej. un pago pendiente)."""

    label: str
    value: str
    when: str | None = None


class StructuredAnswer(BaseModel):
    """Respuesta armada por la capa de presentación, campo por campo.

    El texto que ve la persona se renderiza desde acá: decisión, monto principal,
    explicación breve y —solo si aporta— una única recomendación. Ningún campo interno
    de una tool llega a este modelo sin traducirse a lenguaje natural.
    """

    verdict: AnswerVerdict
    headline: str
    explanation: str = ""
    recommendation: str | None = None
    details: list[AnswerDetail] = Field(default_factory=list)
    how_i_solved_it: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: UUID | None = None
    message: Annotated[str, Field(min_length=1, max_length=1000)]


class ChatResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    answer: str
    structured_answer: StructuredAnswer | None = None
    intent: AgentIntent
    tools_used: list[ToolCallTrace]
    evidence: list[AgentEvidence]
    assumptions: list[str]
    requires_approval: bool
    pending_action: PendingAction | None
    trace_id: str
    # Cuota diaria restante. La adjunta la capa de API, que es la que la reservó; el grafo
    # no la conoce. `None` cuando la operación corrió sin límites (evaluadores, smoke).
    usage: AIUsageMetadata | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # El action_id se valida contra el estado persistido; nunca se confían argumentos nuevos.
    action_id: UUID


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime | None = None


class ConversationResponse(BaseModel):
    conversation_id: UUID
    messages: list[ConversationMessage]
