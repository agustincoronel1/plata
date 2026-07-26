"""Schemas del perfil financiero.

El perfil se consulta y se guarda entero (PUT), no por partes: por eso hay un único
schema de entrada, ProfileUpdate, con todos los campos editables.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import Money, Name, NonNegativeMoney

# Para el MVP el producto es solo Argentina. La moneda existe en el modelo para no
# reescribir el esquema el día que haya más de una, pero hoy el único valor válido es ARS.
SUPPORTED_CURRENCY = "ARS"


class ProfileUpdate(BaseModel):
    """Entrada del PUT /profile: crea el perfil o lo reemplaza por completo."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name
    currency: str = SUPPORTED_CURRENCY
    current_balance: Money = Decimal("0")
    next_income_amount: NonNegativeMoney = Decimal("0")
    next_income_date: date | None = None
    protected_amount: NonNegativeMoney = Decimal("0")
    safety_buffer: NonNegativeMoney = Decimal("0")

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3:
            raise ValueError("La moneda debe tener exactamente tres letras.")
        if normalized != SUPPORTED_CURRENCY:
            raise ValueError(f"Por ahora Plata solo maneja {SUPPORTED_CURRENCY}.")
        return normalized


class ProfileResponse(BaseModel):
    """Salida del perfil. Los montos Decimal se serializan como string."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    currency: str
    current_balance: Money
    next_income_amount: Money
    next_income_date: date | None
    protected_amount: Money
    safety_buffer: Money
    created_at: datetime
    updated_at: datetime
