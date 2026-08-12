"""Endpoints del copiloto financiero: /api/v1/ai/chat y conversaciones.

Todos exigen sesión. Sin un JWT válido se responde 401 antes de correr el grafo: no se
llama al modelo, no se ejecutan tools, no se consulta el RAG y no se escribe nada.

El `conversation_id` viaja por la URL, así que **no** alcanza como identificador del hilo:
el thread del checkpointer se arma con el usuario adentro (ver `ai_chat_service`). Una
conversación de otra cuenta resuelve un hilo vacío, no la conversación ajena.

`chat` tiene cuota diaria; aprobar y rechazar no. Reanudar el grafo para aplicar una
escritura ya aprobada no vuelve a llamar al modelo (va derecho a `apply_write`), así que
no cuesta plata y limitarlo dejaría acciones pendientes imposibles de resolver.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.ai.agent.schemas import (
    ApprovalRequest,
    ChatRequest,
    ChatResponse,
    ConversationResponse,
)
from app.ai.gateway import AIGateway, get_ai_gateway
from app.api.rate_limits import ai_ip_limit, ai_user_limit
from app.api.usage_headers import apply_usage_headers, with_usage
from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.ai_usage import AIUsageResponse
from app.services import ai_chat_service, ai_usage_service
from app.services.draft_store import DraftStore, get_draft_store

# El límite por IP y por hora va en el router para que se resuelva antes que el token.
router = APIRouter(prefix="/ai", tags=["ai-copiloto"], dependencies=[Depends(ai_ip_limit)])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Hablar con el copiloto",
    # Límite por cuenta, aparte de la cuota diaria: la cuota acota el gasto del día y esto
    # acota la ráfaga. Se cuenta el intento aunque el turno lo resuelva el fast path sin
    # llamar al modelo, porque igual costó una petición.
    dependencies=[Depends(ai_user_limit)],
)
def chat(
    payload: ChatRequest,
    current_user: CurrentUser,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    # `quota.consume` se le pasa al servicio en vez de llamarlo acá: el servicio lo
    # ejecuta después de descartar el 409 por acción pendiente, que es una validación
    # que no llega a invocar al modelo y por lo tanto no debe gastar cuota.
    with ai_usage_service.daily_quota(db, current_user.id) as quota:
        answer = ai_chat_service.chat(
            db,
            payload.message,
            payload.conversation_id,
            user_id=current_user.id,
            draft_store=store,
            gateway=gateway,
            before_provider=quota.consume,
        )

    apply_usage_headers(response, quota.status)
    # El servicio no sabe de cuotas: la metadata se adjunta acá, donde se reservó.
    return with_usage(answer, quota.status)


@router.get(
    "/usage",
    response_model=AIUsageResponse,
    summary="Cuántas consultas inteligentes le quedan hoy a la cuenta",
    description=(
        "Uso del día de la cuota compartida por todos los canales de IA, más el umbral a "
        "partir del cual conviene avisar. Solo lectura: consultar no gasta cuota. El día "
        "se corta a las 00:00 de la zona informada en `timezone`."
    ),
)
def get_usage(
    current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> AIUsageResponse:
    return AIUsageResponse.from_status(ai_usage_service.get_status(db, current_user.id))


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Historial de una conversación propia",
)
def get_conversation(conversation_id: UUID, current_user: CurrentUser) -> ConversationResponse:
    return ai_chat_service.get_conversation(conversation_id, user_id=current_user.id)


@router.post(
    "/conversations/{conversation_id}/approve",
    response_model=ChatResponse,
    summary="Aprobar la acción pendiente y reanudar el grafo",
)
def approve(
    conversation_id: UUID,
    payload: ApprovalRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    return ai_chat_service.resume(
        db,
        conversation_id,
        payload.action_id,
        user_id=current_user.id,
        approve=True,
        draft_store=store,
        gateway=gateway,
    )


@router.post(
    "/conversations/{conversation_id}/reject",
    response_model=ChatResponse,
    summary="Rechazar la acción pendiente (no persiste nada)",
)
def reject(
    conversation_id: UUID,
    payload: ApprovalRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    return ai_chat_service.resume(
        db,
        conversation_id,
        payload.action_id,
        user_id=current_user.id,
        approve=False,
        draft_store=store,
        gateway=gateway,
    )
