"""Orquestación del dashboard: lee la base y ejecuta el motor financiero.

No modifica ninguna fila ni hace commit. Convierte el perfil y los compromisos del ORM a
las entradas puras del motor (`ProfileInput` / `CommitmentInput`) y devuelve el resultado
listo para que la capa de API lo valide contra el schema.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models import Commitment, UserProfile
from app.services import commitment_service
from app.services.financial_engine import (
    CommitmentInput,
    ProfileInput,
    build_month_end_forecast,
    build_summary,
)
from app.services.profile_service import get_profile


def to_profile_input(profile: UserProfile) -> ProfileInput:
    return ProfileInput(
        current_balance=profile.current_balance,
        next_income_amount=profile.next_income_amount,
        next_income_date=profile.next_income_date,
        protected_amount=profile.protected_amount,
        safety_buffer=profile.safety_buffer,
    )


def to_commitment_inputs(commitments: list[Commitment]) -> list[CommitmentInput]:
    return [
        CommitmentInput(
            amount=c.amount,
            due_date=c.due_date,
            status=c.status.value,
            is_recurring=c.is_recurring,
        )
        for c in commitments
    ]


def build_dashboard_summary(session: Session, as_of: date | None = None) -> dict[str, Any]:
    """Resumen financiero + proyección de fin de mes del perfil demo.

    Lanza NotFoundError si el perfil no existe. Solo lectura: no toca la base.
    """
    profile = get_profile(session)
    commitments = commitment_service.list_commitments(session)
    today = as_of or date.today()

    profile_input = to_profile_input(profile)
    commitment_inputs = to_commitment_inputs(commitments)

    summary = build_summary(profile_input, commitment_inputs, today)
    forecast = build_month_end_forecast(profile_input, commitment_inputs, today)

    return {**summary, "forecast": forecast}
