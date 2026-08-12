"""Endpoints de autenticación: /api/v1/auth.

Vector no emite ni guarda credenciales: el registro, el login y la renovación del token
ocurren en Supabase Auth. Acá solo se VERIFICA el token que llega y se responde quién es
el usuario, para que el frontend pueda confirmar que el circuito cierra de punta a punta.
"""

from fastapi import APIRouter, Depends

from app.api.rate_limits import auth_ip_limit
from app.core.security import CurrentUser
from app.schemas.auth import AuthenticatedUser

# Límite por IP propio, además del general de la API. Este es el único endpoint del backend
# que se puede usar para probar tokens en serie: el registro y el login ocurren en Supabase
# Auth y no pasan por acá (sus límites se configuran en el panel de Supabase).
router = APIRouter(
    prefix="/auth",
    tags=["autenticación"],
    dependencies=[Depends(auth_ip_limit)],
)


@router.get("/me", response_model=AuthenticatedUser)
def read_current_user(user: CurrentUser) -> AuthenticatedUser:
    """Devuelve la identidad del token verificado. 401 si no hay un token válido.

    No consulta la base ni datos financieros: es la prueba de que la firma, el emisor,
    la audiencia y el vencimiento del JWT se validaron bien. Tampoco devuelve el token
    ni claims de más.
    """
    return user
