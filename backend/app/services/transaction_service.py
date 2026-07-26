"""Lógica de movimientos y su efecto sobre el saldo.

Política de saldo (explícita, testeable, sin callbacks del modelo):

- Crear un income:  current_balance += amount
- Crear un expense: current_balance -= amount
- Editar:  se revierte el efecto anterior y se aplica el del movimiento actualizado.
- Eliminar: se revierte el efecto del movimiento.

Cada operación es atómica: el cambio del movimiento y el del saldo viajan en un único
commit. Ante cualquier error se hace rollback completo y ninguno de los dos queda a
medias. Para evitar carreras entre operaciones concurrentes, el UserProfile se bloquea
con SELECT ... FOR UPDATE antes de tocar el saldo.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import DEMO_USER_ID
from app.models import Transaction, TransactionType, UserProfile
from app.schemas.transaction import TransactionCreate, TransactionUpdate
from app.services.exceptions import NotFoundError
from app.services.profile_service import PROFILE_NOT_FOUND

TRANSACTION_NOT_FOUND = "Movimiento no encontrado"


def _index(session: Session, transaction: Transaction) -> None:
    """Indexa el movimiento para el RAG (best-effort; nunca rompe la escritura)."""
    from app.ai.rag.indexer import index_transaction

    index_transaction(session, transaction)


def _signed_effect(tx_type: TransactionType, amount: Decimal) -> Decimal:
    """Efecto del movimiento sobre el saldo: + si es ingreso, - si es gasto."""
    return amount if tx_type is TransactionType.INCOME else -amount


def _lock_profile(session: Session) -> UserProfile:
    """Bloquea el perfil demo para modificarlo. Lanza NotFoundError si no existe."""
    profile = session.execute(
        select(UserProfile).where(UserProfile.id == DEMO_USER_ID).with_for_update()
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError(PROFILE_NOT_FOUND)
    return profile


def _get_owned(session: Session, transaction_id: UUID) -> Transaction:
    """Movimiento del perfil demo. Lanza NotFoundError si no existe o es de otro perfil."""
    transaction = session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == DEMO_USER_ID,
        )
    ).scalar_one_or_none()
    if transaction is None:
        raise NotFoundError(TRANSACTION_NOT_FOUND)
    return transaction


def list_transactions(session: Session) -> list[Transaction]:
    """Movimientos del perfil demo, del más reciente al más antiguo.

    Ordena por fecha del movimiento y, a igualdad de fecha, por fecha de alta: solo
    lectura, nunca toca el saldo.
    """
    return list(
        session.execute(
            select(Transaction)
            .where(Transaction.user_id == DEMO_USER_ID)
            .order_by(Transaction.occurred_on.desc(), Transaction.created_at.desc())
        ).scalars()
    )


def create_transaction_no_commit(session: Session, payload: TransactionCreate) -> Transaction:
    """Crea el movimiento y aplica su efecto sobre el saldo sin cerrar la transacción."""
    profile = _lock_profile(session)
    transaction = Transaction(user_id=DEMO_USER_ID, **payload.model_dump())

    profile.current_balance += _signed_effect(transaction.type, transaction.amount)
    session.add(transaction)
    session.flush()
    _index(session, transaction)
    return transaction


def create_transaction(session: Session, payload: TransactionCreate) -> Transaction:
    """Crea el movimiento y aplica su efecto sobre el saldo, en un único commit."""
    try:
        transaction = create_transaction_no_commit(session, payload)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(transaction)
    return transaction


def update_transaction(
    session: Session, transaction_id: UUID, payload: TransactionUpdate
) -> Transaction:
    """Edita un movimiento y ajusta el saldo por la diferencia de efecto.

    Se revierte el efecto del movimiento anterior y se aplica el del actualizado. Cambiar
    un gasto por un ingreso, o el monto, recalcula el saldo correctamente.
    """
    profile = _lock_profile(session)
    transaction = _get_owned(session, transaction_id)

    previous_effect = _signed_effect(transaction.type, transaction.amount)
    changes = payload.model_dump(exclude_unset=True)

    try:
        for field, value in changes.items():
            setattr(transaction, field, value)
        new_effect = _signed_effect(transaction.type, transaction.amount)
        profile.current_balance += new_effect - previous_effect
        session.flush()
        _index(session, transaction)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(transaction)
    return transaction


def delete_transaction(session: Session, transaction_id: UUID) -> None:
    """Elimina un movimiento y revierte su efecto sobre el saldo, en un único commit."""
    profile = _lock_profile(session)
    transaction = _get_owned(session, transaction_id)

    try:
        profile.current_balance -= _signed_effect(transaction.type, transaction.amount)
        session.delete(transaction)
        session.commit()
    except Exception:
        session.rollback()
        raise
