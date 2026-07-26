"""Endpoint del dashboard financiero: /api/v1/dashboard/summary."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.dashboard import DashboardSummaryResponse
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Resumen financiero determinístico",
    description=(
        "Calcula, sin modificar nada, el disponible real y el monto diario seguro hasta el "
        "próximo ingreso, más una proyección de fin de mes.\n\n"
        "Fórmulas:\n"
        "- `available_real = current_balance - pending_commitments - protected - safety`\n"
        "- `spendable_total = max(available_real, 0)`\n"
        "- `daily_safe_to_spend = spendable_total / days_until_income` (ROUND_DOWN)\n\n"
        "Los compromisos considerados son los `pending` con vencimiento hasta el horizonte, "
        "incluidos los vencidos que siguen pendientes. `current_balance` ya refleja los "
        "movimientos históricos: no se recalcula. La proyección de fin de mes usa solo "
        "ingresos y compromisos cargados, no estima gastos variables."
    ),
)
def get_dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
) -> DashboardSummaryResponse:
    """Resumen del perfil demo. 404 si el perfil no existe."""
    return dashboard_service.build_dashboard_summary(db)
