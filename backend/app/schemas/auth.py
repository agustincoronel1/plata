"""Schemas de autenticación.

El usuario autenticado se arma SOLO con los claims verificados del JWT de Supabase.
No hay campos que vengan del body, de la query ni de una tabla: si un dato no está
firmado en el token, no está acá.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    """Identidad del usuario de la sesión actual.

    Es también el cuerpo de GET /api/v1/auth/me. A propósito expone lo mínimo: el
    identificador y, si el token lo trae, el email. Nunca el JWT, el rol, la sesión ni
    ningún otro claim: lo que no se necesita, no se devuelve.
    """

    # `sub` del JWT, ya validado como UUID. Es el identificador del usuario en Supabase.
    id: UUID
    # Supabase lo incluye en los tokens de email/contraseña, pero no es obligatorio
    # (otros proveedores pueden no darlo), así que el contrato lo declara opcional.
    email: str | None = None
