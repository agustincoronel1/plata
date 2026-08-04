"""Schemas de compromisos (pagos futuros o pendientes)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import CommitmentStatus
from app.schemas.common import Name, NonEmptyUpdate, PositiveMoney
from app.services.categorizer import resolve_expense_category

OptionalCategory = Annotated[str | None, Field(default=None, max_length=60)]


class CommitmentCreate(BaseModel):
    """Alta de un compromiso.

    No incluye `status`: un compromiso nace siempre `pending`. Si el cliente manda un
    estado, Pydantic lo ignora (extra fields por defecto se descartan) y el servidor
    fuerza `pending`. El user_id lo pone el servidor, nunca el cliente.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name
    amount: PositiveMoney
    due_date: date
    category: OptionalCategory = None
    is_recurring: bool = False

    @model_validator(mode="after")
    def _categorize(self) -> CommitmentCreate:
        self.category = resolve_expense_category(self.category, self.name)
        return self


class CommitmentUpdate(NonEmptyUpdate):
    """Edición parcial de un compromiso. Un body vacío se rechaza.

    Acá sí se puede cambiar `status` a pending / paid / cancelled: es como se marca un
    compromiso pagado o cancelado.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name | None = None
    amount: PositiveMoney | None = None
    due_date: date | None = None
    category: OptionalCategory = None
    status: CommitmentStatus | None = None
    is_recurring: bool | None = None


class CommitmentResponse(BaseModel):
    """Salida de un compromiso. Los montos Decimal se serializan como string."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    amount: PositiveMoney
    due_date: date
    category: str
    status: CommitmentStatus
    is_recurring: bool
    created_at: datetime
    updated_at: datetime
