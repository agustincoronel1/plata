"""Endpoints del perfil financiero: /api/v1/profile.

Todos exigen sesión: el perfil que se lee y se escribe es el de quien hizo la petición,
identificado por el `sub` del JWT verificado. El cliente nunca manda un identificador de
usuario, así que tampoco puede elegir de quién es el perfil.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.rate_limits import write_user_limit
from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.profile import ProfileResponse, ProfileUpdate
from app.services import profile_service

router = APIRouter(prefix="/profile", tags=["perfil"])

# Techo por cuenta para las escrituras. Son acciones MANUALES: no consumen cuota de IA
# ni la rozan. El limite esta muy por encima del uso humano; solo frena la automatizacion.
WRITE_LIMIT = [Depends(write_user_limit)]


@router.get("", response_model=ProfileResponse)
def read_profile(
    current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> ProfileResponse:
    """Devuelve el perfil del usuario. 404 si todavía no fue creado.

    Ese 404 no es un error: es una cuenta recién registrada, y es lo que dispara el
    onboarding en el frontend.
    """
    return profile_service.get_profile(db, user_id=current_user.id)


@router.put("", response_model=ProfileResponse, dependencies=WRITE_LIMIT)
def upsert_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ProfileResponse:
    """Crea el perfil del usuario si no existe, o actualiza todos sus campos editables.

    Responde 200 tanto al crear como al actualizar, para que el frontend no tenga que
    distinguir los dos casos. Al crear, la clave primaria es el UUID de Supabase: es el
    alta del usuario en Vector.
    """
    return profile_service.upsert_profile(db, user_id=current_user.id, payload=payload)
