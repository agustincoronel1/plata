from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user_profile import UserProfile

MONEY = Numeric(14, 2)


class PurchaseSimulation(Base):
    """Simulación de una compra en cuotas. Inmutable: no lleva updated_at."""

    __tablename__ = "purchase_simulations"
    __table_args__ = (
        CheckConstraint("total_amount > 0", name="total_amount_positive"),
        CheckConstraint("installments > 0", name="installments_positive"),
        CheckConstraint("installment_amount > 0", name="installment_amount_positive"),
        # Historial de simulaciones de un usuario, de la más reciente hacia atrás.
        Index("ix_purchase_simulations_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )

    purchase_name: Mapped[str] = mapped_column(String(120), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    installments: Mapped[int] = mapped_column(Integer, nullable=False)
    installment_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    first_installment_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Resultado estructurado del cálculo. El cálculo todavía no existe.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[UserProfile] = relationship(back_populates="purchase_simulations")
