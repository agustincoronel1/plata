"""Endpoints de simulación de compras: /api/v1/simulations.

Exigen sesión. Se simula contra el disponible del usuario autenticado y el historial que
se devuelve es solo el suyo.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.simulation import (
    PurchaseSimulationCreate,
    PurchaseSimulationResponse,
)
from app.services import simulation_service

router = APIRouter(prefix="/simulations", tags=["simulaciones"])


@router.post(
    "/purchase",
    response_model=PurchaseSimulationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Simular una compra en cuotas",
    description=(
        "Proyecta el impacto de una compra financiada mes a mes, la compara con empezar un "
        "mes después y persiste el resultado. El total es el costo final financiado: no se "
        "calculan intereses ni CFT. No modifica el saldo, ni las transacciones, ni los "
        "compromisos. 404 si el perfil no existe."
    ),
)
def create_purchase_simulation(
    payload: PurchaseSimulationCreate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseSimulationResponse:
    return simulation_service.create_purchase_simulation(
        db, user_id=current_user.id, payload=payload
    )


@router.get(
    "",
    response_model=list[PurchaseSimulationResponse],
    summary="Historial de simulaciones",
    description="Las 10 simulaciones más recientes del usuario, de más nueva a más vieja.",
)
def list_purchase_simulations(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[PurchaseSimulationResponse]:
    return simulation_service.list_purchase_simulations(db, user_id=current_user.id)
