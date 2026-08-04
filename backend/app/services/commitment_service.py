"""Lógica de compromisos (pagos futuros o pendientes).

Un compromiso pendiente no toca el saldo: el motor financiero lo descuenta del disponible.
Cuando pasa realmente a pagado, se crea un gasto real vinculado y recién ahí se ajusta el
saldo. Volver desde pagado a otro estado elimina solo ese gasto autogenerado.

`user_id` es obligatorio y sin valor por defecto: las lecturas y escrituras quedan
acotadas al usuario dueño. En la API sale siempre de `current_user.id`, es decir del
JWT ya verificado.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.timezone import app_today
from app.models import Commitment, CommitmentStatus, Transaction, TransactionType
from app.schemas.commitment import CommitmentCreate, CommitmentUpdate
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services import transaction_service
from app.services.categorizer import resolve_expense_category
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


def _get_owned(session: Session, user_id: UUID, commitment_id: UUID) -> Commitment:
    """Compromiso del usuario. NotFoundError si no existe o es de otra persona.

    El filtro por dueño va en la misma consulta que el id, y un compromiso ajeno
    responde 404: nunca se confirma que exista.
    """
    commitment = session.execute(
        select(Commitment).where(
            Commitment.id == commitment_id,
            Commitment.user_id == user_id,
        )
    ).scalar_one_or_none()
    if commitment is None:
        raise NotFoundError(COMMITMENT_NOT_FOUND)
    return commitment


def _get_owned_for_update(session: Session, user_id: UUID, commitment_id: UUID) -> Commitment:
    commitment = session.execute(
        select(Commitment)
        .where(
            Commitment.id == commitment_id,
            Commitment.user_id == user_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if commitment is None:
        raise NotFoundError(COMMITMENT_NOT_FOUND)
    return commitment


def _generated_transaction(
    session: Session, user_id: UUID, commitment_id: UUID
) -> Transaction | None:
    return session.execute(
        select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.commitment_id == commitment_id,
        )
    ).scalar_one_or_none()


def _payment_payload(commitment: Commitment) -> TransactionCreate:
    return TransactionCreate(
        type=TransactionType.EXPENSE,
        amount=commitment.amount,
        category=commitment.category,
        description=commitment.name,
        occurred_on=app_today(),
    )


def _create_payment_transaction(session: Session, user_id: UUID, commitment: Commitment) -> None:
    if _generated_transaction(session, user_id, commitment.id) is not None:
        return
    transaction_service.create_transaction_no_commit(
        session,
        user_id,
        _payment_payload(commitment),
        commitment_id=commitment.id,
    )


def _delete_payment_transaction(session: Session, user_id: UUID, commitment: Commitment) -> None:
    transaction = _generated_transaction(session, user_id, commitment.id)
    if transaction is None:
        return
    transaction_service.delete_transaction_no_commit(session, user_id, transaction.id)


def _sync_payment_transaction(session: Session, user_id: UUID, commitment: Commitment) -> None:
    transaction = _generated_transaction(session, user_id, commitment.id)
    if transaction is None:
        return
    transaction_service.update_transaction_no_commit(
        session,
        user_id,
        transaction.id,
        TransactionUpdate(
            type=TransactionType.EXPENSE,
            amount=commitment.amount,
            category=commitment.category,
            description=commitment.name,
        ),
    )


def _resolved_update_changes(
    commitment: Commitment, changes: dict[str, object]
) -> dict[str, object]:
    if "category" not in changes:
        return changes
    raw_category = changes["category"]
    name = changes.get("name", commitment.name)
    changes["category"] = resolve_expense_category(
        str(raw_category) if raw_category is not None else None,
        str(name) if name is not None else commitment.name,
    )
    return changes


def list_commitments(session: Session, user_id: UUID) -> list[Commitment]:
    """Compromisos del usuario en orden determinista.

    Primero los pendientes por vencimiento ascendente, después pagados y cancelados. El
    desempate final por fecha de alta hace el orden estable ante empates.
    """
    return list(
        session.execute(
            select(Commitment)
            .where(Commitment.user_id == user_id)
            .order_by(_STATUS_ORDER, Commitment.due_date.asc(), Commitment.created_at.asc())
        ).scalars()
    )


def create_commitment_no_commit(
    session: Session, user_id: UUID, payload: CommitmentCreate
) -> Commitment:
    """Crea un compromiso sin cerrar la transacción. No toca el saldo."""
    if get_profile_or_none(session, user_id) is None:
        raise NotFoundError(PROFILE_NOT_FOUND)

    commitment = Commitment(
        user_id=user_id,
        status=CommitmentStatus.PENDING,
        **payload.model_dump(),
    )
    session.add(commitment)
    session.flush()
    return commitment


def create_commitment(session: Session, user_id: UUID, payload: CommitmentCreate) -> Commitment:
    """Crea un compromiso, siempre con status pending. No toca el saldo."""
    try:
        commitment = create_commitment_no_commit(session, user_id, payload)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(commitment)
    return commitment


def update_commitment(
    session: Session, user_id: UUID, commitment_id: UUID, payload: CommitmentUpdate
) -> Commitment:
    """Edita un compromiso, incluido su status (pending / paid / cancelled).

    El paso no pagado -> paid crea un gasto real vinculado. La reversión desde paid borra
    solo ese gasto autogenerado. Editar un compromiso ya pagado sincroniza su movimiento.
    """
    commitment = _get_owned_for_update(session, user_id, commitment_id)
    changes = _resolved_update_changes(commitment, payload.model_dump(exclude_unset=True))
    previous_status = commitment.status

    try:
        for field, value in changes.items():
            setattr(commitment, field, value)
        session.flush()

        became_paid = (
            previous_status is not CommitmentStatus.PAID
            and commitment.status is CommitmentStatus.PAID
        )
        left_paid = (
            previous_status is CommitmentStatus.PAID
            and commitment.status is not CommitmentStatus.PAID
        )

        if became_paid:
            _create_payment_transaction(session, user_id, commitment)
        elif left_paid:
            _delete_payment_transaction(session, user_id, commitment)
        elif commitment.status is CommitmentStatus.PAID:
            _sync_payment_transaction(session, user_id, commitment)

        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(commitment)
    return commitment


def delete_commitment(session: Session, user_id: UUID, commitment_id: UUID) -> None:
    """Elimina un compromiso. No toca el saldo."""
    commitment = _get_owned(session, user_id, commitment_id)
    try:
        session.delete(commitment)
        session.commit()
    except Exception:
        session.rollback()
        raise
