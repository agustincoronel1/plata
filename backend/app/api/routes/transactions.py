"""Endpoints de movimientos: /api/v1/transactions.

Todos exigen sesión y operan exclusivamente sobre los movimientos de quien hizo la
petición. El identificador sale del JWT verificado (`current_user.id`): el cliente no lo
manda ni puede elegirlo, ni por body, ni por query, ni por header.

Un id de movimiento ajeno responde 404, igual que uno inexistente: el filtro por dueño
viaja en la misma consulta (ver `transaction_service`).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
)
from app.services import transaction_service

router = APIRouter(prefix="/transactions", tags=["movimientos"])


@router.get("", response_model=list[TransactionResponse])
def list_transactions(
    current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> list[TransactionResponse]:
    """Movimientos del usuario, del más reciente al más antiguo."""
    return transaction_service.list_transactions(db, user_id=current_user.id)


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """Crea un movimiento y actualiza el saldo del usuario. 404 si su perfil no existe."""
    return transaction_service.create_transaction(db, user_id=current_user.id, payload=payload)


@router.patch("/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """Edita un movimiento propio y ajusta el saldo. 404 si no existe o es de otra persona."""
    return transaction_service.update_transaction(
        db, user_id=current_user.id, transaction_id=transaction_id, payload=payload
    )


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: UUID,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Elimina un movimiento propio y revierte su efecto sobre el saldo."""
    transaction_service.delete_transaction(
        db, user_id=current_user.id, transaction_id=transaction_id
    )
