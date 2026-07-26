"""Endpoints de registro asistido por IA: /api/v1/ai/transactions.

`parse` y `confirm` son DOS operaciones separadas y explícitas:

- `parse` interpreta texto y devuelve un borrador (200). No crea nada ni toca el saldo.
- `confirm` requiere confirmación humana y recién ahí crea el movimiento (201), reusando
  `transaction_service`. El saldo se actualiza una sola vez.
- `reject` descarta el borrador (204). No toca la base.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway, get_ai_gateway
from app.core.database import get_db
from app.schemas.ai_transaction import (
    TransactionConfirmationResponse,
    TransactionConfirmRequest,
    TransactionParseRequest,
    TransactionParseResponse,
)
from app.services import ai_transaction_service
from app.services.draft_store import DraftStore, get_draft_store

router = APIRouter(prefix="/ai/transactions", tags=["ai-transactions"])


@router.post(
    "/parse",
    response_model=TransactionParseResponse,
    summary="Interpretar un movimiento desde texto (no lo guarda)",
    description=(
        "Interpreta una frase en español rioplatense y devuelve un BORRADOR editable. No "
        "modifica el saldo ni crea el movimiento: para eso está `confirm`. La IA propone, "
        "una persona confirma."
    ),
)
def parse_transaction(
    payload: TransactionParseRequest,
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> TransactionParseResponse:
    return ai_transaction_service.parse_transaction(gateway, store, payload.text)


@router.post(
    "/{draft_id}/confirm",
    response_model=TransactionConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirmar un borrador y registrar el movimiento",
    description=(
        "Con confirmación explícita (`confirmed=true`) y correcciones opcionales, crea el "
        "movimiento real reusando el servicio de transacciones y actualiza el saldo una "
        "sola vez. 404 si el borrador no existe, 410 si expiró, 409 si ya se usó, 422 si "
        "los datos no forman un movimiento válido."
    ),
)
def confirm_transaction(
    draft_id: UUID,
    payload: TransactionConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> TransactionConfirmationResponse:
    return ai_transaction_service.confirm_transaction(db, store, draft_id, payload)


@router.post(
    "/{draft_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rechazar un borrador",
    description="Descarta el borrador. No crea movimiento ni modifica el saldo.",
)
def reject_transaction(
    draft_id: UUID,
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> None:
    ai_transaction_service.reject_transaction(store, draft_id)
