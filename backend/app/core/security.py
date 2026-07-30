"""Verificación del JWT de Supabase Auth.

El frontend se autentica contra Supabase y manda el access token en cada petición:

    Authorization: Bearer <access_token>

Acá se verifica ese token de punta a punta antes de considerar que hay un usuario:

1. El header existe y usa el esquema Bearer.
2. La FIRMA es válida contra la clave pública correspondiente del JWKS del proyecto.
3. El token no expiró (claim `exp`).
4. El emisor (`iss`) es el del proyecto configurado.
5. La audiencia (`aud`) es la esperada.
6. El `sub` existe y es un UUID.

Reglas duras de este módulo:

- NUNCA se decodifica un token sin verificar la firma. No se usa `verify_signature=False`.
- NUNCA se confía en un `user_id` que venga del body, de la query o de un header propio:
  la identidad sale exclusivamente del token firmado.
- La publishable key del frontend NO es un secreto de firma y no se usa acá. Tampoco se
  usa la service_role key: el backend solo necesita el JWKS público.
- El motivo exacto del rechazo queda en el log (nivel debug), no en la respuesta: al
  cliente siempre le llega el mismo 401 con un texto pensado para el usuario.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient

from app.core.config import settings
from app.schemas.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

# Algoritmos asimétricos que emite Supabase (ECC P-256 por defecto en proyectos nuevos,
# RSA en los que rotaron desde una clave RSA). La lista es cerrada a propósito: sin ella,
# un token con alg "none" o con un HMAC firmado con un valor conocido sería aceptable.
ALLOWED_ALGORITHMS = ["RS256", "ES256"]

# Claims obligatorios. Si falta cualquiera, PyJWT corta antes de que miremos nada.
REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]

# Un único texto para todos los rechazos: no le cuenta a un atacante si falló la firma,
# el emisor o el vencimiento, y al usuario legítimo le dice qué hacer.
UNAUTHORIZED_DETAIL = "Tu sesión no es válida o expiró. Iniciá sesión de nuevo."

NOT_CONFIGURED_DETAIL = "La autenticación no está configurada en el servidor."


def _unauthorized(reason: str) -> HTTPException:
    """401 con el mismo detalle siempre; el motivo real solo va al log."""
    logger.debug("JWT rechazado: %s", reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=UNAUTHORIZED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    """Cliente JWKS compartido, creado una sola vez por proceso.

    El cacheo vive en el propio PyJWKClient: guarda el set de claves durante
    `supabase_jwks_cache_seconds` en lugar de pedirlo en cada request, y ante un `kid`
    que no conoce vuelve a descargarlo. Eso es lo que hace que una rotación de claves en
    Supabase se resuelva sola, sin reiniciar el backend.
    """
    return PyJWKClient(
        settings.supabase_jwks_url,
        cache_keys=True,
        lifespan=settings.supabase_jwks_cache_seconds,
    )


def reset_jwks_client() -> None:
    """Olvida el cliente cacheado. Para tests y para recargar la configuración."""
    get_jwks_client.cache_clear()


def _bearer_token(request: Request) -> str:
    """Extrae el token del header Authorization. Cualquier desvío es 401, no 400."""
    header = request.headers.get("Authorization")
    if not header:
        raise _unauthorized("falta el header Authorization")

    parts = header.split()
    if len(parts) != 2:
        raise _unauthorized("formato del header Authorization inválido")

    scheme, token = parts
    if scheme.lower() != "bearer":
        raise _unauthorized(f"esquema no soportado: {scheme!r}")

    return token


def get_current_user(request: Request) -> AuthenticatedUser:
    """Dependencia de FastAPI: devuelve el usuario de la sesión o corta con 401.

    Es la única puerta de entrada a la identidad del usuario. Un endpoint que la declare
    puede asumir que el `id` que recibe pertenece a quien hizo la petición.
    """
    token = _bearer_token(request)

    if not settings.supabase_jwks_url or not settings.supabase_jwt_issuer:
        # Falta configuración del servidor: no es culpa del cliente y no es un 401.
        # Importante que sea explícito: sin esto, un backend mal configurado podría
        # parecer "todos los tokens son inválidos" en lugar de "falta el JWKS".
        logger.error("Supabase Auth sin configurar: SUPABASE_JWKS_URL / SUPABASE_JWT_ISSUER")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=NOT_CONFIGURED_DETAIL,
        )

    try:
        # Lee solo el header del token (el `kid`) para elegir la clave pública. Los claims
        # siguen sin leerse: recién los tocamos después de verificar la firma.
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
    except Exception as error:  # PyJWKClientError, urllib, token ilegible…
        raise _unauthorized(
            f"no se pudo resolver la clave de firma ({type(error).__name__})"
        ) from error

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            issuer=settings.supabase_jwt_issuer,
            audience=settings.supabase_jwt_audience,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as error:
        # Firma inválida, expirado, issuer o audience distintos, claim faltante: todo
        # termina en el mismo 401.
        raise _unauthorized(f"token inválido ({type(error).__name__})") from error

    subject = claims.get("sub")
    try:
        user_id = UUID(str(subject))
    except (AttributeError, TypeError, ValueError):
        raise _unauthorized("el claim sub no es un UUID") from None

    email = claims.get("email")
    return AuthenticatedUser(id=user_id, email=email if isinstance(email, str) else None)


# Alias tipado para las firmas de los endpoints: `user: CurrentUser`.
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
