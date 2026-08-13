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
    SPENDING_SUMMARY = "spending_summary"
    # "¿Por qué me dijiste eso?": se explica la respuesta anterior con lo que ya se calculó,
    # sin volver a ejecutar la intención entera ni pedir tools de nuevo.
    EXPLAIN_LAST_ANSWER = "explain_last_answer"
    # Compra al contado (pago único hoy) vs. compra financiada en cuotas: son caminos
    # distintos a propósito. Una consulta al contado nunca debe pasar por el simulador.
    ONE_TIME_PURCHASE = "one_time_purchase"
    SIMULATE_PURCHASE = "simulate_purchase"
    COMPARE_PURCHASE_DATES = "compare_purchase_dates"
    CREATE_TRANSACTION = "create_transaction"
    CREATE_COMMITMENT = "create_commitment"
    # Charla dentro del dominio financiero que NO depende de los datos de la persona:
    # qué es un fondo de emergencia, si conviene pagar en cuotas sin interés, cómo
    # ordenarse. Es una capacidad válida del copiloto, no un cajón de descarte: para eso
    # está UNKNOWN, que sigue significando "esto no lo puedo atender".
    CONVERSATIONAL = "conversational"
    UNKNOWN = "unknown"


class AgentRoute(StrEnum):
    """Cómo se resuelve un turno. Es la pieza que faltaba en el contrato.

    Antes había una sola cadena (clasificar → tools → plantilla → verificar) y todo lo que
    no encajaba terminaba en un mensaje de error. La ruta dice explícitamente qué clase de
    turno es, y con eso cada capa sabe qué exigirle:

    - `DETERMINISTIC`: la respuesta ES un dato de la persona. Números de SQL, plantilla.
    - `SIMULATION`: el motor determinístico proyecta un escenario.
    - `ACTION`: escritura; pasa por borrador y aprobación humana.
    - `CLARIFICATION`: falta un dato para poder resolver. NO es un error: es una pregunta.
    - `CONVERSATIONAL`: se contesta hablando, sin tocar la base.
    - `MIXED`: se traen datos reales y después se razona sobre ellos en lenguaje natural.
    - `UNSUPPORTED`: fuera de alcance o intento de manipular al agente.
    - `ERROR`: falla real (proveedor caído, salida corrupta, tool rota, estado imposible).
    """

    DETERMINISTIC = "deterministic"
    SIMULATION = "simulation"
    ACTION = "action"
    CLARIFICATION = "clarification"
    CONVERSATIONAL = "conversational"
    MIXED = "mixed"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


# Rutas cuyo texto se arma con datos del usuario y por lo tanto exige respaldo en tools.
GROUNDED_ROUTES = frozenset(
    {AgentRoute.DETERMINISTIC, AgentRoute.SIMULATION, AgentRoute.MIXED, AgentRoute.ACTION}
)

# Rutas donde el texto lo escribe el modelo y no hay plantilla que lo reemplace.
FREE_TEXT_ROUTES = frozenset({AgentRoute.CONVERSATIONAL, AgentRoute.CLARIFICATION})


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

# Datos que el copiloto puede llegar a pedir. Es una lista cerrada porque el modelo la
# elige dentro de un structured output: un campo inventado sí es una salida inválida.
MissingField = Literal["amount", "installments", "name", "due_date", "category"]


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
    # Cómo se resolvió el turno. Es información de diagnóstico (la interfaz no la muestra) y
    # tiene default, así que ninguna respuesta que ya existía cambia de forma.
    route: AgentRoute = AgentRoute.DETERMINISTIC
    tools_used: list[ToolCallTrace]
    evidence: list[AgentEvidence]
    assumptions: list[str]
    requires_approval: bool
    pending_action: PendingAction | None
    trace_id: str
    # Cuota diaria restante. La adjunta la capa de API, que es la que la reservó; el grafo
    # no la conoce. `None` cuando la operación corrió sin límites (evaluadores, smoke).
    usage: AIUsageMetadata | None = None
    # Quién resolvió el turno: el agente completo o el atajo determinístico previo
    # (`app.ai.fast_path`). Es para depurar y para medir qué porcentaje de consultas
    # evita el modelo; la interfaz no lo muestra. Tiene default, así que ninguna
    # respuesta que ya existía cambia de forma.
    source: Literal["agent", "fast_path"] = "agent"


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
