"""Lógica del perfil financiero.

No depende de FastAPI. Recibe una Session, opera y hace commit o rollback. La API solo
traduce el resultado (o la excepción de dominio) a HTTP.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import DEMO_USER_ID
from app.models import UserProfile
from app.schemas.profile import ProfileUpdate
from app.services.exceptions import NotFoundError

PROFILE_NOT_FOUND = "Perfil financiero no encontrado"

# Campos del perfil que el cliente puede editar. El id y los timestamps quedan afuera.
_EDITABLE_FIELDS = (
    "name",
    "currency",
    "current_balance",
    "next_income_amount",
    "next_income_date",
    "protected_amount",
    "safety_buffer",
)


def get_profile(session: Session) -> UserProfile:
    """Devuelve el perfil demo o lanza NotFoundError si todavía no fue creado."""
    profile = session.get(UserProfile, DEMO_USER_ID)
    if profile is None:
        raise NotFoundError(PROFILE_NOT_FOUND)
    return profile


def get_profile_or_none(session: Session) -> UserProfile | None:
    """Igual que get_profile pero sin lanzar: útil para chequear existencia."""
    return session.get(UserProfile, DEMO_USER_ID)


def upsert_profile(session: Session, payload: ProfileUpdate) -> UserProfile:
    """Crea el perfil demo si no existe, o reemplaza todos sus campos editables.

    Devuelve el perfil ya persistido. El saldo se toma tal cual lo manda el cliente: el
    PUT del perfil es una edición directa de la situación, no un movimiento.
    """
    profile = session.execute(
        select(UserProfile).where(UserProfile.id == DEMO_USER_ID).with_for_update()
    ).scalar_one_or_none()

    values = payload.model_dump()
    try:
        if profile is None:
            profile = UserProfile(id=DEMO_USER_ID, **values)
            session.add(profile)
        else:
            for field in _EDITABLE_FIELDS:
                setattr(profile, field, values[field])
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(profile)
    return profile
