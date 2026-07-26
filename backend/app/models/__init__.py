"""Modelos de SQLAlchemy.

Importar este paquete registra las cuatro tablas en `Base.metadata`. Alembic depende
de eso para el autogenerate.
"""

from app.models.ai_draft import AIDraft
from app.models.commitment import Commitment
from app.models.enums import CommitmentStatus, TransactionType
from app.models.purchase_simulation import PurchaseSimulation
from app.models.transaction import Transaction
from app.models.transaction_search import TransactionSearchDocument
from app.models.user_profile import UserProfile

__all__ = [
    "AIDraft",
    "Commitment",
    "CommitmentStatus",
    "PurchaseSimulation",
    "Transaction",
    "TransactionSearchDocument",
    "TransactionType",
    "UserProfile",
]
