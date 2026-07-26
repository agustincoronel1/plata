"""Schemas de simulación de compras en cuotas.

La entrada es lo mínimo que el usuario conoce: el total final financiado, en cuántas
cuotas y desde cuándo. El motor calcula el resto. `installment_amount`, `result` y
`user_id` nunca se aceptan desde el cliente.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Name, PositiveMoney


class PurchaseSimulationCreate(BaseModel):
    """Entrada de una simulación. El total es el costo final financiado, sin intereses."""

    model_config = ConfigDict(str_strip_whitespace=True)

    purchase_name: Name
    total_amount: PositiveMoney
    installments: Annotated[int, Field(ge=1, le=24)]
    first_installment_date: date

    @field_validator("first_installment_date")
    @classmethod
    def _not_in_the_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("La primera cuota no puede ser anterior a hoy.")
        return value


class PurchaseSimulationResponse(BaseModel):
    """Salida de una simulación persistida.

    `result` es el cálculo completo del motor (calendario, proyección mensual, comparación
    con empezar el mes siguiente), ya serializado: montos como string, fechas como ISO.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    purchase_name: str
    total_amount: Decimal
    installments: int
    installment_amount: Decimal
    first_installment_date: date
    created_at: datetime
    result: dict[str, Any]
