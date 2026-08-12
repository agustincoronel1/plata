import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/")
def root() -> dict[str, str]:
    """Presentación de la API."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "message": "No te dice solamente cuánto dinero tenés. Te dice cuánto podés usar.",
    }


@router.get("/health")
def health() -> dict[str, str]:
    """Healthcheck de la API: no toca PostgreSQL."""
    return {
        "status": "ok",
        # Renombrado en el rebranding. Es seguro: el frontend valida `status`, no `service`
        # (ver `fetchApiHealth` en services/api.js), y Render solo mira el código HTTP.
        "service": "vector-api",
        "version": settings.app_version,
    }


@router.get("/health/db")
def health_db(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Healthcheck de la base: 503 si PostgreSQL no responde."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # El detalle queda en los logs del servidor, nunca en la respuesta:
        # incluye host, usuario y a veces la contraseña.
        logger.exception("El healthcheck de base de datos falló")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de datos no disponible",
        ) from None

    return {"status": "ok", "database": "connected"}
