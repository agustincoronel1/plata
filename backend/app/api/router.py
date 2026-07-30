from fastapi import APIRouter

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

api_router = APIRouter()

# Rutas de negocio. Cuelgan del prefijo /api/v1 (ver app.main).
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(transactions.router)
api_router.include_router(commitments.router)
api_router.include_router(dashboard.router)
api_router.include_router(simulations.router)
api_router.include_router(ai_transactions.router)
api_router.include_router(ai_chat.router)
