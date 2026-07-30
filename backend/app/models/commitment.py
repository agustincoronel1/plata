from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import CommitmentStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user_profile import UserProfile

MONEY = Numeric(14, 2)


class Commitment(TimestampMixin, Base):
    """Plata que ya tiene dueño: alquiler, cuota, servicio, suscripción."""

    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # El cálculo del disponible filtra por usuario, vencimiento y estado.
        Index("ix_commitments_user_id_due_date_status", "user_id", "due_date", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[CommitmentStatus] = mapped_column(
        Enum(
            CommitmentStatus,
            name="commitment_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CommitmentStatus.PENDING,
        server_default=text("'pending'"),
    )
    # El motor lo proyecta en cada mes simulado; no se generan filas para las próximas
    # ocurrencias.
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    user: Mapped[UserProfile] = relationship(back_populates="commitments")
