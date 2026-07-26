"""Schemas de compromisos (pagos futuros o pendientes)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import CommitmentStatus
from app.schemas.common import Category, Name, NonEmptyUpdate, PositiveMoney


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
    category: Category
    is_recurring: bool = False


class CommitmentUpdate(NonEmptyUpdate):
    """Edición parcial de un compromiso. Un body vacío se rechaza.

    Acá sí se puede cambiar `status` a pending / paid / cancelled: es como se marca un
    compromiso pagado o cancelado.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name | None = None
    amount: PositiveMoney | None = None
    due_date: date | None = None
    category: Category | None = None
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
