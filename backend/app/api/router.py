from fastapi import APIRouter

from app.api.routes import (
    ai_chat,
    ai_transactions,
    commitments,
    dashboard,
    profile,
    simulations,
    transactions,
)

api_router = APIRouter()

# Rutas de negocio. Cuelgan del prefijo /api/v1 (ver app.main).
# Día 2: perfil, movimientos, compromisos. Día 3: dashboard y simulaciones.
# Día 4: registro asistido por IA (parse / confirm / reject).
api_router.include_router(profile.router)
api_router.include_router(transactions.router)
api_router.include_router(commitments.router)
api_router.include_router(dashboard.router)
api_router.include_router(simulations.router)
api_router.include_router(ai_transactions.router)
api_router.include_router(ai_chat.router)
