"""Semilla determinística para los evaluadores que necesitan PostgreSQL.

Abre una transacción, siembra un perfil + movimientos etiquetados y hace rollback al final:
nada persiste. Si PostgreSQL no está disponible, el evaluador lo informa y sale con 0 (una
base caída no es un fallo de métrica).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.constants import DEMO_USER_ID
from app.core.database import engine
from app.models import UserProfile
from app.schemas.commitment import CommitmentCreate
from app.schemas.transaction import TransactionCreate
from app.services import commitment_service, transaction_service

SEED_TX = [
    ("transporte", "12000", "carga de nafta"),
    ("transporte", "15000", "nafta ruta"),
    ("gastronomía", "8000", "cafe con leche"),
    ("supermercado", "42000", "compra semanal super"),
    ("servicios", "60000", "pago de gas"),
]


def postgres_available() -> bool:
    try:
        conn = engine.connect()
        conn.close()
    except SQLAlchemyError:
        return False
    return True


@contextmanager
def seeded_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    existing = session.get(UserProfile, DEMO_USER_ID)
    if existing is not None:
        session.delete(existing)
        session.flush()
    session.add(
        UserProfile(
            id=DEMO_USER_ID,
            name="Demo",
            current_balance=Decimal("600000"),
            next_income_amount=Decimal("1200000"),
            next_income_date=date(2026, 8, 1),
            protected_amount=Decimal("100000"),
            safety_buffer=Decimal("40000"),
        )
    )
    session.flush()
    for category, amount, desc in SEED_TX:
        transaction_service.create_transaction(
            session,
            TransactionCreate(
                type="expense",
                amount=Decimal(amount),
                category=category,
                description=desc,
                occurred_on=date(2026, 7, 20),
            ),
        )
    commitment_service.create_commitment(
        session,
        CommitmentCreate(
            name="Alquiler",
            amount=Decimal("300000"),
            due_date=date(2026, 7, 30),
            category="vivienda",
        ),
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
