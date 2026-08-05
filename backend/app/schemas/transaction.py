"""Schemas de movimientos (ingresos y gastos ya ocurridos)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timezone import app_today
from app.models.enums import TransactionType
from app.schemas.common import Category, NonEmptyUpdate, PositiveMoney, empty_to_none
from app.services.categorizer import OTHER_CATEGORY, resolve_expense_category

# description y payment_method son opcionales: llegan recortados (max_length evita chocar
# con String(255)/String(60) de la base) y, si quedan vacíos, se guardan como NULL.

# La categoría de un GASTO es opcional en la entrada: si no llega (o llega fuera del
# vocabulario fijo), la resuelven las reglas determinísticas de `categorizer` a partir del
# texto disponible. Los INGRESOS conservan su categoría de texto libre.
OptionalCategory = Annotated[str | None, Field(default=None, max_length=60)]


def _resolve_category(
    tx_type: TransactionType, category: str | None, description: str | None
) -> str:
    if tx_type is TransactionType.EXPENSE:
        return resolve_expense_category(category, description)
    normalized = (category or "").strip().lower()
    return normalized or OTHER_CATEGORY


def _no_future(value: date) -> date:
    """Un movimiento ya ocurrió: su fecha no puede estar en el futuro.

    "Hoy" es el día de Argentina, no el del servidor: Render corre en UTC y adelanta el
    calendario tres horas. Con `date.today()` acá, un gasto de las 22:00 quedaría fechado
    al día siguiente y la fecha de un pago calculada en hora argentina podría verse como
    futura desde el servidor.
    """
    if value > app_today():
        raise ValueError("La fecha del movimiento no puede ser futura.")
    return value


class TransactionCreate(BaseModel):
    """Alta de un movimiento. El user_id lo pone el servidor, nunca el cliente."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: TransactionType
    amount: PositiveMoney
    category: OptionalCategory = None
    description: str | None = Field(default=None, max_length=255)
    occurred_on: date = Field(default_factory=app_today)
    payment_method: str | None = Field(default=None, max_length=60)

    _clean_description = field_validator("description")(empty_to_none)
    _clean_payment_method = field_validator("payment_method")(empty_to_none)
    _check_occurred_on = field_validator("occurred_on")(_no_future)

    @model_validator(mode="after")
    def _categorize(self) -> TransactionCreate:
        """Deja la categoría siempre resuelta: nunca se guarda un gasto sin categoría."""
        self.category = _resolve_category(self.type, self.category, self.description)
        return self


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
    commitment_id: UUID | None = None
    type: TransactionType
    amount: PositiveMoney
    currency: str
    category: str
    description: str | None
    occurred_on: date
    payment_method: str | None
    created_at: datetime
    updated_at: datetime
