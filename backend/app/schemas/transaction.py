"""Schemas de movimientos (ingresos y gastos ya ocurridos)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TransactionType
from app.schemas.common import Category, NonEmptyUpdate, PositiveMoney, empty_to_none

# description y payment_method son opcionales: llegan recortados (max_length evita chocar
# con String(255)/String(60) de la base) y, si quedan vacíos, se guardan como NULL.


def _no_future(value: date) -> date:
    """Un movimiento ya ocurrió: su fecha no puede estar en el futuro."""
    if value > date.today():
        raise ValueError("La fecha del movimiento no puede ser futura.")
    return value


class TransactionCreate(BaseModel):
    """Alta de un movimiento. El user_id lo pone el servidor, nunca el cliente."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: TransactionType
    amount: PositiveMoney
    category: Category
    description: str | None = Field(default=None, max_length=255)
    occurred_on: date = Field(default_factory=date.today)
    payment_method: str | None = Field(default=None, max_length=60)

    _clean_description = field_validator("description")(empty_to_none)
    _clean_payment_method = field_validator("payment_method")(empty_to_none)
    _check_occurred_on = field_validator("occurred_on")(_no_future)


class TransactionUpdate(NonEmptyUpdate):
    """Edición parcial de un movimiento. Todo es opcional; un body vacío se rechaza."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: TransactionType | None = None
    amount: PositiveMoney | None = None
    category: Category | None = None
    description: str | None = Field(default=None, max_length=255)
    occurred_on: date | None = None
    payment_method: str | None = Field(default=None, max_length=60)

    _clean_description = field_validator("description")(empty_to_none)
    _clean_payment_method = field_validator("payment_method")(empty_to_none)

    @field_validator("occurred_on")
    @classmethod
    def _check_occurred_on(cls, value: date | None) -> date | None:
        return _no_future(value) if value is not None else None


class TransactionResponse(BaseModel):
    """Salida de un movimiento. Los montos Decimal se serializan como string."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    type: TransactionType
    amount: PositiveMoney
    category: str
    description: str | None
    occurred_on: date
    payment_method: str | None
    created_at: datetime
    updated_at: datetime
