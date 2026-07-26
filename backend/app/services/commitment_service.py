"""Lógica de compromisos (pagos futuros o pendientes).

Decisión de producto del Día 2, documentada para que no haya ambigüedad: los
compromisos NO tocan current_balance. Crearlos, editarlos, marcarlos pagados o
cancelarlos no genera ninguna transacción ni modifica el saldo. El efecto de los
compromisos sobre el dinero disponible se calcula recién en el Día 3, cuando exista el
motor financiero. Por eso este servicio no bloquea ni lee el perfil para nada más que
verificar que exista al crear.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.constants import DEMO_USER_ID
from app.models import Commitment, CommitmentStatus
from app.schemas.commitment import CommitmentCreate, CommitmentUpdate
from app.services.exceptions import NotFoundError
from app.services.profile_service import PROFILE_NOT_FOUND, get_profile_or_none

COMMITMENT_NOT_FOUND = "Compromiso no encontrado"

# Orden de presentación: primero lo que hay que pagar (pending), después lo resuelto.
_STATUS_ORDER = case(
    (Commitment.status == CommitmentStatus.PENDING, 0),
    (Commitment.status == CommitmentStatus.PAID, 1),
    (Commitment.status == CommitmentStatus.CANCELLED, 2),
    else_=3,
)


def _get_owned(session: Session, commitment_id: UUID) -> Commitment:
    """Compromiso del perfil demo. Lanza NotFoundError si no existe o es de otro perfil."""
    commitment = session.execute(
        select(Commitment).where(
            Commitment.id == commitment_id,
            Commitment.user_id == DEMO_USER_ID,
        )
    ).scalar_one_or_none()
    if commitment is None:
        raise NotFoundError(COMMITMENT_NOT_FOUND)
    return commitment


def list_commitments(session: Session) -> list[Commitment]:
    """Compromisos del perfil demo en orden determinista.

    Primero los pendientes por vencimiento ascendente, después pagados y cancelados. El
    desempate final por fecha de alta hace el orden estable ante empates.
    """
    return list(
        session.execute(
            select(Commitment)
            .where(Commitment.user_id == DEMO_USER_ID)
            .order_by(_STATUS_ORDER, Commitment.due_date.asc(), Commitment.created_at.asc())
        ).scalars()
    )


def create_commitment_no_commit(session: Session, payload: CommitmentCreate) -> Commitment:
    """Crea un compromiso sin cerrar la transacción. No toca el saldo."""
    if get_profile_or_none(session) is None:
        raise NotFoundError(PROFILE_NOT_FOUND)

    commitment = Commitment(
        user_id=DEMO_USER_ID,
        status=CommitmentStatus.PENDING,
        **payload.model_dump(),
    )
    session.add(commitment)
    session.flush()
    return commitment


def create_commitment(session: Session, payload: CommitmentCreate) -> Commitment:
    """Crea un compromiso, siempre con status pending. No toca el saldo."""
    try:
        commitment = create_commitment_no_commit(session, payload)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(commitment)
    return commitment


def update_commitment(
    session: Session, commitment_id: UUID, payload: CommitmentUpdate
) -> Commitment:
    """Edita un compromiso, incluido su status (pending / paid / cancelled).

    Marcar paid o cancelled es solo un cambio de estado: no crea transacción ni modifica
    el saldo.
    """
    commitment = _get_owned(session, commitment_id)
    changes = payload.model_dump(exclude_unset=True)

    try:
        for field, value in changes.items():
            setattr(commitment, field, value)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(commitment)
    return commitment


def delete_commitment(session: Session, commitment_id: UUID) -> None:
    """Elimina un compromiso. No toca el saldo."""
    commitment = _get_owned(session, commitment_id)
    try:
        session.delete(commitment)
        session.commit()
    except Exception:
        session.rollback()
        raise
