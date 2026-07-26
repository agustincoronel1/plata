"""Contrato del copiloto: intents, evidencia y request/response del chat."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentIntent(StrEnum):
    DASHBOARD_SUMMARY = "dashboard_summary"
    EXPLAIN_AVAILABLE_MONEY = "explain_available_money"
    LIST_COMMITMENTS = "list_commitments"
    SEARCH_HISTORY = "search_history"
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


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: UUID | None = None
    message: Annotated[str, Field(min_length=1, max_length=1000)]


class ChatResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    answer: str
    intent: AgentIntent
    tools_used: list[ToolCallTrace]
    evidence: list[AgentEvidence]
    assumptions: list[str]
    requires_approval: bool
    pending_action: PendingAction | None
    trace_id: str


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
