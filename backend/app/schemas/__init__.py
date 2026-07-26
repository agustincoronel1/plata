"""Schemas de Pydantic 2: el contrato de entrada y salida de la API."""

from app.schemas.ai_transaction import (
    AIIntent,
    ParsedTransactionDraft,
    TransactionConfirmationResponse,
    TransactionConfirmRequest,
    TransactionParseModelOutput,
    TransactionParseRequest,
    TransactionParseResponse,
)
from app.schemas.commitment import (
    CommitmentCreate,
    CommitmentResponse,
    CommitmentUpdate,
)
from app.schemas.dashboard import (
    DashboardSummaryResponse,
    MonthEndForecastResponse,
)
from app.schemas.profile import ProfileResponse, ProfileUpdate
from app.schemas.simulation import (
    PurchaseSimulationCreate,
    PurchaseSimulationResponse,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
)

__all__ = [
    "AIIntent",
    "CommitmentCreate",
    "CommitmentResponse",
    "CommitmentUpdate",
    "DashboardSummaryResponse",
    "MonthEndForecastResponse",
    "ParsedTransactionDraft",
    "ProfileResponse",
    "ProfileUpdate",
    "PurchaseSimulationCreate",
    "PurchaseSimulationResponse",
    "TransactionConfirmRequest",
    "TransactionConfirmationResponse",
    "TransactionCreate",
    "TransactionParseModelOutput",
    "TransactionParseRequest",
    "TransactionParseResponse",
    "TransactionResponse",
    "TransactionUpdate",
]
