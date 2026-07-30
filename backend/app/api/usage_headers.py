"""Estado de la cuota diaria de IA en las respuestas.

Viaja por dos vías, y cada una tiene su razón:

- **Cabeceras**: las lee un cliente HTTP genérico y, sobre todo, están también en el 429,
  donde no hay cuerpo de respuesta normal. El frontend las lee en un solo lugar
  (`api.js` → `services/aiUsage.js`).
- **Campo `usage` del cuerpo**: es el lugar natural para que la propia respuesta cuente
  cuánta cuota queda. Es un campo agregado y opcional: ninguno de los campos que ya
  devolvían `/ai/chat` y `/ai/transactions/parse` cambió.

Ninguna de las dos incluye el `user_id`.
"""

from __future__ import annotations

from fastapi import Response
from pydantic import BaseModel

from app.core.config import settings
from app.schemas.ai_usage import AIUsageMetadata
from app.services.ai_usage_service import AIUsageStatus

LIMIT_HEADER = "X-AI-Daily-Limit"
REMAINING_HEADER = "X-AI-Daily-Remaining"
KIND_HEADER = "X-AI-Daily-Kind"
# El umbral del aviso viaja con la respuesta para que el número viva en un solo lugar (la
# configuración del backend) y el frontend no tenga que repetirlo.
WARN_AT_HEADER = "X-AI-Daily-Warn-At"

# Sin esto el navegador no deja leer las cabeceras desde otro origen: `Access-Control-
# Allow-Origin` habilita la respuesta, pero solo los headers "seguros" quedan visibles
# para JavaScript. Las propias van declaradas en `Access-Control-Expose-Headers`.
EXPOSED_HEADERS = [LIMIT_HEADER, REMAINING_HEADER, KIND_HEADER, WARN_AT_HEADER]


def with_usage[ModelT: BaseModel](payload: ModelT, status: AIUsageStatus | None) -> ModelT:
    """Devuelve la respuesta con el campo `usage` completo.

    Se hace acá y no en los servicios de IA porque la cuota es una decisión de la capa
    pública: el grafo y el parser no saben que existe un límite, y así siguen sirviendo
    igual a los evaluadores offline, que corren sin cuota.

    `model_copy` en lugar de mutar: los schemas de respuesta se tratan como inmutables.
    """
    if status is None:
        return payload
    return payload.model_copy(update={"usage": AIUsageMetadata.from_status(status)})


def apply_usage_headers_to_dict(headers: dict[str, str], status: AIUsageStatus) -> None:
    """Misma información, sobre un dict. La usa el manejador del 429, que arma su respuesta."""
    headers[LIMIT_HEADER] = str(status.limit)
    headers[REMAINING_HEADER] = str(status.remaining)
    headers[KIND_HEADER] = str(status.kind)
    headers[WARN_AT_HEADER] = str(settings.ai_usage_warning_threshold)


def apply_usage_headers(response: Response, status: AIUsageStatus | None) -> None:
    """Publica el estado de la cuota en la respuesta. Si no se consumió nada, no hace nada."""
    if status is None:
        return

    apply_usage_headers_to_dict(response.headers, status)
