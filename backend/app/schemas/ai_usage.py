"""Schema del estado de la cuota diaria de consultas inteligentes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.core.config import settings
from app.services.ai_usage_service import AIUsageStatus


class AIUsageMetadata(BaseModel):
    """Estado de la cuota que viaja dentro de una respuesta de IA.

    Es un campo **opcional y agregado**: los campos que ya devolvían `/ai/chat` y
    `/ai/transactions/parse` no cambiaron. A propósito no incluye `user_id`: quien recibe la
    respuesta ya sabe quién es, y publicarlo solo agregaría un dato identificatorio de más.

    `resets_at` y `reset_at` son el mismo instante con dos nombres: el primero es el que ya
    consumía el frontend, el segundo el nombre del contrato de la cuota. `timezone` viaja
    para que quien lea la respuesta sepa en qué zona se cortó el día sin tener que asumirla.
    """

    limit: int
    used: int
    remaining: int
    warning: bool
    resets_at: datetime
    reset_at: datetime
    timezone: str

    @classmethod
    def from_status(cls, status: AIUsageStatus) -> AIUsageMetadata:
        return cls(
            limit=status.limit,
            used=status.used,
            remaining=status.remaining,
            warning=status.warning,
            resets_at=status.resets_at,
            reset_at=status.resets_at,
            timezone=status.timezone,
        )


class AIUsageResponse(BaseModel):
    """Cuerpo de GET /api/v1/ai/usage.

    Solo expone el contador de quien pregunta. `warning_threshold` viaja acá para que el
    umbral del aviso viva en un solo lugar (la configuración del backend) y el frontend no
    tenga que repetir el número. Consultarlo NO gasta cuota.
    """

    limit: int
    used: int
    remaining: int
    warning_threshold: int
    resets_at: datetime
    reset_at: datetime
    timezone: str

    @classmethod
    def from_status(cls, status: AIUsageStatus) -> AIUsageResponse:
        return cls(
            limit=status.limit,
            used=status.used,
            remaining=status.remaining,
            warning_threshold=settings.ai_usage_warning_threshold,
            resets_at=status.resets_at,
            reset_at=status.resets_at,
            timezone=status.timezone,
        )
