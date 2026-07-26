"""Endpoints del copiloto financiero: /api/v1/ai/chat y conversaciones."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.agent.schemas import (
    ApprovalRequest,
    ChatRequest,
    ChatResponse,
    ConversationResponse,
)
from app.ai.gateway import AIGateway, get_ai_gateway
from app.core.database import get_db
from app.services import ai_chat_service
from app.services.draft_store import DraftStore, get_draft_store

router = APIRouter(prefix="/ai", tags=["ai-copiloto"])


@router.post("/chat", response_model=ChatResponse, summary="Hablar con el copiloto")
def chat(
    payload: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    return ai_chat_service.chat(
        db, payload.message, payload.conversation_id, draft_store=store, gateway=gateway
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Historial de una conversación",
)
def get_conversation(conversation_id: UUID) -> ConversationResponse:
    return ai_chat_service.get_conversation(conversation_id)


@router.post(
    "/conversations/{conversation_id}/approve",
    response_model=ChatResponse,
    summary="Aprobar la acción pendiente y reanudar el grafo",
)
def approve(
    conversation_id: UUID,
    payload: ApprovalRequest,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    return ai_chat_service.resume(
        db, conversation_id, payload.action_id, approve=True, draft_store=store, gateway=gateway
    )


@router.post(
    "/conversations/{conversation_id}/reject",
    response_model=ChatResponse,
    summary="Rechazar la acción pendiente (no persiste nada)",
)
def reject(
    conversation_id: UUID,
    payload: ApprovalRequest,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
) -> ChatResponse:
    return ai_chat_service.resume(
        db, conversation_id, payload.action_id, approve=False, draft_store=store, gateway=gateway
    )
