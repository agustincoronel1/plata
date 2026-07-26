"""Endpoints del perfil financiero: /api/v1/profile."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.profile import ProfileResponse, ProfileUpdate
from app.services import profile_service

router = APIRouter(prefix="/profile", tags=["perfil"])


@router.get("", response_model=ProfileResponse)
def read_profile(db: Annotated[Session, Depends(get_db)]) -> ProfileResponse:
    """Devuelve el perfil demo. 404 si todavía no fue creado."""
    return profile_service.get_profile(db)


@router.put("", response_model=ProfileResponse)
def upsert_profile(
    payload: ProfileUpdate, db: Annotated[Session, Depends(get_db)]
) -> ProfileResponse:
    """Crea el perfil demo si no existe, o actualiza todos sus campos editables.

    Responde 200 tanto al crear como al actualizar, para que el frontend no tenga que
    distinguir los dos casos.
    """
    return profile_service.upsert_profile(db, payload)
