"""Endpoints de movimientos: /api/v1/transactions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
)
from app.services import transaction_service

router = APIRouter(prefix="/transactions", tags=["movimientos"])


@router.get("", response_model=list[TransactionResponse])
def list_transactions(db: Annotated[Session, Depends(get_db)]) -> list[TransactionResponse]:
    """Movimientos del perfil demo, del más reciente al más antiguo."""
    return transaction_service.list_transactions(db)


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate, db: Annotated[Session, Depends(get_db)]
) -> TransactionResponse:
    """Crea un movimiento y actualiza el saldo del perfil. 404 si el perfil no existe."""
    return transaction_service.create_transaction(db, payload)


@router.patch("/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """Edita un movimiento del perfil demo y ajusta el saldo. 404 si no existe."""
    return transaction_service.update_transaction(db, transaction_id, payload)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(transaction_id: UUID, db: Annotated[Session, Depends(get_db)]) -> None:
    """Elimina un movimiento del perfil demo y revierte su efecto sobre el saldo."""
    transaction_service.delete_transaction(db, transaction_id)
