from fastapi import APIRouter, Depends

from app.api.rate_limits import api_ip_limit
from app.api.routes import (
    ai_chat,
    ai_transactions,
    auth,
    commitments,
    dashboard,
    profile,
    simulations,
    transactions,
)

# Techo general por IP para toda la API. Va acá, como dependencia del router, para que se
# resuelva ANTES que la verificación del token: así un flood sin sesión (o con una sesión
# inválida) se corta igual, en lugar de gastar una verificación de JWT por petición.
#
# `/health` y `/` no cuelgan de este router (los sirve `system.router` directo desde
# app.main), así que el healthcheck de Render y el poller de arranque en frío del frontend
# no consumen este límite.
api_router = APIRouter(dependencies=[Depends(api_ip_limit)])

# Rutas de negocio. Cuelgan del prefijo /api/v1 (ver app.main).
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(transactions.router)
api_router.include_router(commitments.router)
api_router.include_router(dashboard.router)
api_router.include_router(simulations.router)
api_router.include_router(ai_transactions.router)
api_router.include_router(ai_chat.router)
