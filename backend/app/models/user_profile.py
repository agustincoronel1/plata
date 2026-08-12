from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.commitment import Commitment
    from app.models.purchase_simulation import PurchaseSimulation
    from app.models.transaction import Transaction

MONEY = Numeric(14, 2)


class UserProfile(TimestampMixin, Base):
    """Perfil financiero de un usuario. Es también su identidad dentro de Vector.

    La clave primaria es el `sub` del JWT de Supabase, así que no hay tabla de usuarios
    aparte ni columna `user_id` acá: el perfil ES el usuario. Se crea la primera vez que
    alguien completa el onboarding (`PUT /api/v1/profile`).

    Movimientos, compromisos y simulaciones cuelgan de este id con `ON DELETE CASCADE`:
    borrar el perfil se lleva todos los datos de esa persona, y solo los de esa persona.
    """

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("next_income_amount >= 0", name="next_income_amount_non_negative"),
        CheckConstraint("protected_amount >= 0", name="protected_amount_non_negative"),
        CheckConstraint("safety_buffer >= 0", name="safety_buffer_non_negative"),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        # current_balance no lleva constraint a propósito: una persona puede estar en
        # rojo y el producto tiene que poder representarlo.
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="ARS", server_default=text("'ARS'")
    )

    current_balance: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default=text("0")
    )
    next_income_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default=text("0")
    )
    next_income_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Reserva de ahorro: se resta del disponible. Un solo monto, sin metas múltiples.
    protected_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default=text("0")
    )
    safety_buffer: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default=text("0")
    )

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    commitments: Mapped[list[Commitment]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    purchase_simulations: Mapped[list[PurchaseSimulation]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("currency")
    def _uppercase_currency(self, key: str, value: str) -> str:
        return value.upper() if value else value
