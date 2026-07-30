"""Persistencia de simulaciones de compra.

Ejecuta el motor financiero (puro) y guarda la simulación como una fila de
PurchaseSimulation, con el resultado completo en JSONB ya serializado (sin Decimal ni
date crudos). No modifica el saldo, ni las transacciones, ni los compromisos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PurchaseSimulation
from app.schemas.simulation import PurchaseSimulationCreate
from app.services import commitment_service
from app.services.dashboard_service import to_commitment_inputs, to_profile_input
from app.services.exceptions import NotFoundError
from app.services.financial_engine import simulate_purchase, to_jsonable
from app.services.profile_service import PROFILE_NOT_FOUND, get_profile_or_none

# Las 10 simulaciones más recientes; todavía no hay paginación.
RECENT_LIMIT = 10


def create_purchase_simulation(
    session: Session,
    user_id: UUID,
    payload: PurchaseSimulationCreate,
    as_of: date | None = None,
) -> PurchaseSimulation:
    """Corre la simulación, la persiste y la devuelve. Un commit; rollback si falla.

    Simula con el perfil y los compromisos DEL USUARIO: el disponible contra el que se
    compara la compra es el suyo, no el de otra persona.
    """
    profile = get_profile_or_none(session, user_id)
    if profile is None:
        raise NotFoundError(PROFILE_NOT_FOUND)

    commitments = commitment_service.list_commitments(session, user_id)
    today = as_of or date.today()

    result = simulate_purchase(
        to_profile_input(profile),
        to_commitment_inputs(commitments),
        payload.total_amount,
        payload.installments,
        payload.first_installment_date,
        today,
    )

    # La cuota regular representa el valor típico. En una compra ínfima el redondeo hacia
    # abajo podría dar 0; en ese caso se usa la mayor cuota para respetar amount > 0.
    installment_amount: Decimal = result["regular_installment_amount"]
    if installment_amount <= Decimal("0"):
        installment_amount = max(item["amount"] for item in result["schedule"])

    simulation = PurchaseSimulation(
        user_id=user_id,
        purchase_name=payload.purchase_name,
        total_amount=payload.total_amount,
        installments=payload.installments,
        installment_amount=installment_amount,
        first_installment_date=payload.first_installment_date,
        result=to_jsonable(result),
    )

    try:
        session.add(simulation)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(simulation)
    return simulation


def list_purchase_simulations(session: Session, user_id: UUID) -> list[PurchaseSimulation]:
    """Las simulaciones del usuario, de la más reciente a la más antigua (máx. 10)."""
    return list(
        session.execute(
            select(PurchaseSimulation)
            .where(PurchaseSimulation.user_id == user_id)
            .order_by(PurchaseSimulation.created_at.desc())
            .limit(RECENT_LIMIT)
        ).scalars()
    )
