import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.ai.exceptions import (
    DAILY_LIMIT_CODE,
    AIDailyLimitReachedError,
    AIDraftValidationError,
    AIError,
)
from app.api.router import api_router
from app.api.routes import system
from app.api.usage_headers import EXPOSED_HEADERS, apply_usage_headers_to_dict
from app.core.config import settings
from app.core.rate_limit import RATE_LIMIT_CODE, RateLimitExceededError
from app.services.exceptions import NotFoundError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    """Arranque y cierre del checkpointer del copiloto.

    Se inicializa acá y no en la primera petición para que el DDL de `setup()` y la
    aplicación de RLS ocurran una sola vez, con la aplicación todavía sin tráfico, en lugar
    de en medio del primer mensaje de alguien.

    Un fallo no impide arrancar: Plata funciona sin IA y tumbar todo porque el copiloto no
    puede checkpointear sería peor. Queda registrado como error y el copiloto responde 503
    hasta que la base vuelva. Lo que NO pasa es caer a memoria en silencio.
    """
    from app.ai.agent.checkpointer import start_checkpointer
    from app.ai.agent.graph import close_checkpointer

    start_checkpointer()
    try:
        yield
    finally:
        close_checkpointer()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# La lista de orígenes es cerrada (ver `Settings.cors_allowed_origins`): nunca "*".
# `allow_credentials=False` porque la sesión viaja en `Authorization`, no en cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["OPTIONS", "GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    # Sin esto, el navegador recibe las cabeceras de cuota pero no deja que JavaScript las
    # lea: solo los headers "seguros" quedan visibles cross-origin.
    expose_headers=[*EXPOSED_HEADERS, "Retry-After"],
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

    Nunca se filtra el prompt, la respuesta del modelo, la API key ni detalles del SDK.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(AIDailyLimitReachedError)
def handle_ai_daily_limit(request: Request, exc: AIDailyLimitReachedError) -> JSONResponse:
    """Cuota diaria de consultas inteligentes agotada: 429 con todo lo necesario.

    El `detail` es un objeto y no un string porque el frontend necesita más que el texto:
    el límite, cuánto se usó, cuándo se renueva, en qué zona y un `code` estable.

    Va aparte del manejador de `AIError` aunque sea una subclase: Starlette elige el más
    específico, así que el resto de los errores de IA siguen respondiendo igual.
    """
    usage = exc.usage
    detail: dict[str, object] = {"code": DAILY_LIMIT_CODE, "message": exc.detail}
    headers: dict[str, str] = {}

    if usage is not None:
        detail.update(
            {
                "limit": usage.limit,
                "used": usage.used,
                "remaining": usage.remaining,
                # Mismo instante con los dos nombres: `resets_at` es el que ya leía el
                # frontend, `reset_at` el del contrato de la cuota.
                "resets_at": usage.resets_at.isoformat(),
                "reset_at": usage.resets_at.isoformat(),
                "timezone": usage.timezone,
            }
        )
        headers["Retry-After"] = str(usage.retry_after_seconds)
        # Las mismas cabeceras que en una respuesta exitosa: así el frontend actualiza el
        # contador con el 429 igual que con un 200, sin un camino especial.
        apply_usage_headers_to_dict(headers, usage)

    # Envuelto en `detail` como el resto de la API: el frontend siempre lee ahí, sea un
    # string (los demás errores) o un objeto (este).
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=headers)


@app.exception_handler(RateLimitExceededError)
def handle_rate_limit(request: Request, exc: RateLimitExceededError) -> JSONResponse:
    """Demasiadas peticiones: 429 con `Retry-After`.

    Mismo código HTTP que la cuota diaria de IA pero con otro `code`, para que el frontend
    sepa cuál de los dos fue sin mirar el texto: uno se resuelve esperando unos segundos y
    el otro recién mañana.

    No se dice qué límite se alcanzó ni cuánto queda: eso le serviría a quien esté probando
    hasta dónde puede llegar. El scope real queda en el log del servidor.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": RATE_LIMIT_CODE, "message": exc.detail}},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


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
