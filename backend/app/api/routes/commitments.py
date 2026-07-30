"""Endpoints de compromisos: /api/v1/commitments.

Marcar un compromiso como pagado o cancelado es un PATCH de su status. No crea ninguna
transacción ni modifica el saldo: ver la nota del commitment_service.

Todos exigen sesión y operan sobre los compromisos de quien hizo la petición, según el
`sub` del JWT verificado. Un compromiso ajeno responde 404, igual que uno inexistente.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.commitment import (
    CommitmentCreate,
    CommitmentResponse,
    CommitmentUpdate,
)
from app.services import commitment_service

router = APIRouter(prefix="/commitments", tags=["compromisos"])


@router.get("", response_model=list[CommitmentResponse])
def list_commitments(
    current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> list[CommitmentResponse]:
    """Compromisos del usuario: primero los pendientes por vencimiento."""
    return commitment_service.list_commitments(db, user_id=current_user.id)


@router.post("", response_model=CommitmentResponse, status_code=status.HTTP_201_CREATED)
def create_commitment(
    payload: CommitmentCreate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> CommitmentResponse:
    """Crea un compromiso, siempre con status pending. 404 si el perfil no existe."""
    return commitment_service.create_commitment(db, user_id=current_user.id, payload=payload)


@router.patch("/{commitment_id}", response_model=CommitmentResponse)
def update_commitment(
    commitment_id: UUID,
    payload: CommitmentUpdate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> CommitmentResponse:
    """Edita un compromiso propio, incluido su status. No toca el saldo. 404 si no existe."""
    return commitment_service.update_commitment(
        db, user_id=current_user.id, commitment_id=commitment_id, payload=payload
    )


@router.delete("/{commitment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_commitment(
    commitment_id: UUID,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Elimina un compromiso propio. 404 si no existe o es de otra persona."""
    commitment_service.delete_commitment(db, user_id=current_user.id, commitment_id=commitment_id)
