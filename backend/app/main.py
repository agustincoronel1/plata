import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.ai.exceptions import AIDraftValidationError, AIError
from app.api.router import api_router
from app.api.routes import system
from app.core.config import settings
from app.services.exceptions import NotFoundError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        from app.ai.agent.graph import close_checkpointer

        close_checkpointer()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    """Un recurso inexistente es un 404 con el mensaje de dominio, listo para mostrar."""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(AIDraftValidationError)
def handle_ai_draft_validation(request: Request, exc: AIDraftValidationError) -> JSONResponse:
    """Un borrador inválido es un 422. Si hay errores por campo, viajan como el `detail`
    de FastAPI (lista) para ubicarlos junto al input; si no, un mensaje seguro."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.errors if exc.errors else exc.detail},
    )


@app.exception_handler(AIError)
def handle_ai_error(request: Request, exc: AIError) -> JSONResponse:
    """Errores del flujo de IA: cada uno trae su código HTTP y un detalle seguro.

    Nunca se filtra el prompt, la respuesta cruda del modelo, la API key ni detalles del
    SDK: solo un texto pensado para el usuario, con el fallback al formulario manual.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(SQLAlchemyError)
def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Cualquier error de PostgreSQL que se escape se convierte en un 500 genérico.

    El detalle real —SQL, constraint, credenciales— queda solo en los logs del servidor,
    nunca en la respuesta.
    """
    logger.exception("Error inesperado de base de datos")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Ocurrió un error inesperado. Intentá de nuevo."},
    )


app.include_router(system.router)
app.include_router(api_router, prefix=settings.api_v1_prefix)
