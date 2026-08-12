"""Lógica del perfil financiero.

No depende de FastAPI. Recibe una Session, opera y hace commit o rollback. La API solo
traduce el resultado (o la excepción de dominio) a HTTP.

`user_id` es obligatorio y no tiene valor por defecto, a propósito: sin default no existe
la posibilidad de que un llamador termine operando sobre el perfil de otra persona por
olvidarse de pasarlo. En la API ese identificador sale siempre del JWT verificado
(`current_user.id`), nunca del cuerpo, la query ni un header.

El perfil ES el usuario: su `id` es el `sub` de Supabase. Por eso no hay una tabla de
usuarios aparte ni una columna `user_id` acá.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def get_profile(session: Session, user_id: UUID) -> UserProfile:
    """Devuelve el perfil del usuario o lanza NotFoundError si todavía no fue creado.

    Que falte no es un error: es una cuenta recién creada, y el 404 es lo que dispara el
    onboarding en el frontend.
    """
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError(PROFILE_NOT_FOUND)
    return profile


def get_profile_or_none(session: Session, user_id: UUID) -> UserProfile | None:
    """Igual que get_profile pero sin lanzar: útil para chequear existencia."""
    return session.get(UserProfile, user_id)


def upsert_profile(session: Session, user_id: UUID, payload: ProfileUpdate) -> UserProfile:
    """Crea el perfil del usuario si no existe, o reemplaza todos sus campos editables.

    Devuelve el perfil ya persistido. El saldo se toma tal cual lo manda el cliente: el
    PUT del perfil es una edición directa de la situación, no un movimiento.

    Es también el alta del usuario en Vector: la primera vez que alguien completa el
    onboarding, esta función crea la fila con su UUID de Supabase como clave primaria.
    """
    profile = session.execute(
        select(UserProfile).where(UserProfile.id == user_id).with_for_update()
    ).scalar_one_or_none()

    values = payload.model_dump()
    try:
        if profile is None:
            profile = UserProfile(id=user_id, **values)
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
