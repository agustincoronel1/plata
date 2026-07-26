"""Contrato estructurado del flujo asistido por IA (Día 4).

Dos operaciones separadas y explícitas:

1. **parse**: interpreta texto libre y devuelve un BORRADOR (`TransactionParseResponse`).
   No toca la base ni el saldo.
2. **confirm**: con confirmación humana, persiste el movimiento reusando
   `transaction_service`. El saldo cambia una sola vez.

El modelo produce `TransactionParseModelOutput` (el structured output). El servicio le
agrega los metadatos de trazabilidad (`draft_id`, `prompt_version`, `provider`, ...) para
componer la respuesta. El modelo nunca aporta `user_id`, `id` ni el saldo.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TransactionType
from app.schemas.common import PositiveMoney
from app.schemas.transaction import TransactionResponse

# Moneda: en este MVP solo ARS. El modelo no puede proponer otra.
SUPPORTED_CURRENCY = "ARS"

# Confianza como Decimal (nunca float en el dominio), entre 0 y 1.
Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=3, decimal_places=2)]

# Límites del texto de entrada.
MIN_TEXT_LENGTH = 3
MAX_TEXT_LENGTH = 1000


class AIIntent(StrEnum):
    """Intención detectada. Acotada a propósito: la IA no descubre acciones nuevas."""

    CREATE_TRANSACTION = "create_transaction"
    UNKNOWN = "unknown"


class ParsedTransactionDraft(BaseModel):
    """Los campos de un movimiento tal como los propone la IA. Todo puede faltar.

    `amount` puede ser null cuando el texto no lo dice; el schema distingue "borrador
    incompleto" de "movimiento confirmable" (ver `is_confirmable`). El servicio fija
    `source_text` de forma autoritativa: el modelo no puede falsearlo.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    type: TransactionType | None = None
    amount: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    category: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=255)
    occurred_on: date | None = None
    payment_method: str | None = Field(default=None, max_length=60)
    currency: str = SUPPORTED_CURRENCY
    source_text: str = ""

    @field_validator("category")
    @classmethod
    def _lower_category(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value else None


class TransactionParseModelOutput(BaseModel):
    """Structured output que produce el modelo (mock o real). Se valida con Pydantic.

    No incluye metadatos de infraestructura (draft_id, provider, latencia): esos los
    agrega el servicio. Un `confidence` fuera de [0, 1] hace fallar la validación, lo que
    el gateway traduce a un error de salida estructurada.
    """

    intent: AIIntent
    transaction: ParsedTransactionDraft | None = None
    confidence: Confidence
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    explanation: str = Field(default="", max_length=500)


class TransactionParseResponse(BaseModel):
    """Respuesta de `parse`: el borrador + trazabilidad. Requiere confirmación humana."""

    draft_id: UUID
    intent: AIIntent
    transaction: ParsedTransactionDraft | None
    confidence: Confidence
    missing_fields: list[str]
    ambiguities: list[str]
    explanation: str
    # Siempre true si hay un movimiento propuesto: nada se guarda sin confirmación.
    requires_confirmation: bool
    # Si el borrador ya tiene lo mínimo para confirmarse sin correcciones.
    is_confirmable: bool
    prompt_version: str
    provider: str
    model: str
    latency_ms: int


class TransactionParseRequest(BaseModel):
    """Entrada de `parse`: solo texto. No se aceptan campos financieros."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=MIN_TEXT_LENGTH, max_length=MAX_TEXT_LENGTH)


class TransactionCorrections(BaseModel):
    """Correcciones humanas permitidas antes de confirmar.

    Se limitan a los campos de un movimiento. `extra="forbid"` rechaza cualquier intento
    de colar `user_id`, `id` o el saldo (devuelve 422). Vacío = confirmar tal cual.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: TransactionType | None = None
    amount: PositiveMoney | None = None
    category: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=255)
    occurred_on: date | None = None
    payment_method: str | None = Field(default=None, max_length=60)


class TransactionConfirmRequest(BaseModel):
    """Entrada de `confirm`: confirmación explícita + correcciones opcionales."""

    model_config = ConfigDict(extra="forbid")

    # Debe ser exactamente true: confirmar es un acto deliberado.
    confirmed: bool
    corrections: TransactionCorrections | None = None


class TransactionConfirmationResponse(BaseModel):
    """Respuesta de `confirm`: el movimiento real creado + de dónde salió cada dato.

    Incluye el `TransactionResponse` completo para que el frontend actualice la lista sin
    reconstruir el movimiento ni recalcular el saldo.
    """

    draft_id: UUID
    transaction: TransactionResponse
    # Campos que quedaron tal como los propuso la IA y campos que corrigió el usuario.
    ai_fields: list[str]
    corrected_fields: list[str]
    source_text: str
    created_at: datetime
