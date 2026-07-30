"""Schema del estado de la cuota diaria de IA."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.core.config import settings
from app.services.ai_usage_service import AIUsageKind, AIUsageStatus


class AIUsageMetadata(BaseModel):
    """Estado de la cuota que viaja dentro de una respuesta de IA.

    Es un campo **opcional y agregado**: los campos que ya devolvían `/ai/chat` y
    `/ai/transactions/parse` no cambiaron. A propósito no incluye `user_id`: quien recibe la
    respuesta ya sabe quién es, y publicarlo solo agregaría un dato identificatorio de más.
    """

    kind: str
    limit: int
    used: int
    remaining: int
    warning: bool
    resets_at: datetime

    @classmethod
    def from_status(cls, status: AIUsageStatus) -> AIUsageMetadata:
        return cls(
            kind=str(status.kind),
            limit=status.limit,
            used=status.used,
            remaining=status.remaining,
            warning=status.warning,
            resets_at=status.resets_at,
        )


class AIUsageCounter(BaseModel):
    """Cuota de una operación: cuánto se usó hoy y cuánto queda."""

    limit: int
    used: int
    remaining: int


class AIUsageResponse(BaseModel):
    """Cuerpo de GET /api/v1/ai/usage.

    Solo expone los contadores de quien pregunta. `warning_threshold` viaja acá para que
    el umbral del aviso viva en un solo lugar (la configuración del backend) y el frontend
    no tenga que repetir el número.
    """

    copilot_chat: AIUsageCounter
    transaction_parse: AIUsageCounter
    warning_threshold: int

    @classmethod
    def from_status(cls, statuses: dict[AIUsageKind, AIUsageStatus]) -> AIUsageResponse:
        def counter(kind: AIUsageKind) -> AIUsageCounter:
            status = statuses[kind]
            return AIUsageCounter(limit=status.limit, used=status.used, remaining=status.remaining)

        return cls(
            copilot_chat=counter(AIUsageKind.COPILOT_CHAT),
            transaction_parse=counter(AIUsageKind.TRANSACTION_PARSE),
            warning_threshold=settings.ai_usage_warning_threshold,
        )
