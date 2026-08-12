"""Dependencias de FastAPI que aplican el rate limiting a los endpoints.

Hay dos formas de acotar y se usan para cosas distintas:

- **Por IP** (`ip_rate_limit`): protege de quien todavía no es nadie. Va como dependencia
  DEL ROUTER, así se resuelve ANTES que `get_current_user` y frena un flood aunque el token
  sea inválido o no exista. Es lo único que se puede acotar de nuestro lado para el circuito
  de credenciales: el registro y el login los atiende Supabase Auth y nunca llegan acá.

- **Por cuenta** (`user_rate_limit`): protege de un usuario legítimo que automatiza. Va como
  dependencia DEL ENDPOINT porque necesita la identidad ya verificada, y el sujeto que
  cuenta es siempre `current_user.id`, o sea el `sub` del JWT. Nunca un identificador que
  mande el cliente.

Los límites se leen de `settings` en cada petición, no al importar el módulo: así se pueden
ajustar por entorno (y los tests pueden moverlos) sin tocar código.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import RateLimitRule, client_ip, hit
from app.core.security import CurrentUser

MINUTE = 60
HOUR = 3600


def ip_rate_limit(scope: str, limit: Callable[[], int], window_seconds: int) -> Callable:
    """Límite por IP. Para usar en `APIRouter(dependencies=[Depends(...)])`."""

    def dependency(request: Request, db: Annotated[Session, Depends(get_db)]) -> None:
        hit(db, RateLimitRule(scope, limit(), window_seconds), client_ip(request))

    return dependency


def user_rate_limit(scope: str, limit: Callable[[], int], window_seconds: int) -> Callable:
    """Límite por cuenta autenticada. El sujeto sale del token, nunca del cliente."""

    def dependency(current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> None:
        hit(db, RateLimitRule(scope, limit(), window_seconds), str(current_user.id))

    return dependency


# --- Reglas concretas ---

# Techo general de la API por IP. Red de contención contra scripts: una persona usando
# Vector no se le acerca.
api_ip_limit = ip_rate_limit("ip:api", lambda: settings.rate_limit_ip_per_minute, MINUTE)

# Verificación de tokens. Es lo más parecido a un endpoint de credenciales que expone este
# backend, así que lleva su propio techo además del general.
auth_ip_limit = ip_rate_limit("ip:auth", lambda: settings.rate_limit_auth_ip_per_minute, MINUTE)

# Endpoints que invocan al modelo. Doble límite a propósito: por cuenta, para que nadie
# automatice su propia sesión; y por IP por hora, para que abrir cuentas gratis en serie no
# alcance para multiplicar el gasto de IA.
ai_ip_limit = ip_rate_limit("ip:ai", lambda: settings.rate_limit_ai_ip_per_hour, HOUR)
ai_user_limit = user_rate_limit("user:ai", lambda: settings.rate_limit_ai_user_per_minute, MINUTE)

# Escrituras del dominio. No consumen cuota de IA (son acciones manuales) pero sí crean
# filas, así que llevan un techo por cuenta bien por encima del uso humano.
write_user_limit = user_rate_limit(
    "user:write", lambda: settings.rate_limit_write_user_per_minute, MINUTE
)
