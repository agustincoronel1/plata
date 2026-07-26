from enum import StrEnum


class TransactionType(StrEnum):
    """Signo de un movimiento ya ocurrido."""

    INCOME = "income"
    EXPENSE = "expense"


class CommitmentStatus(StrEnum):
    """Ciclo de vida de un compromiso pendiente."""

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
