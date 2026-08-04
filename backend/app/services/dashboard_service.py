"""Orquestación del dashboard: lee la base y ejecuta el motor financiero.

No modifica ninguna fila ni hace commit. Convierte el perfil y los compromisos del ORM a
las entradas puras del motor (`ProfileInput` / `CommitmentInput`) y devuelve el resultado
listo para que la capa de API lo valide contra el schema.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Commitment, Transaction, TransactionType, UserProfile
from app.services import commitment_service
from app.services.categorizer import OTHER_CATEGORY
from app.services.financial_engine import (
    CommitmentInput,
    ProfileInput,
    build_month_end_forecast,
    build_summary,
)
from app.services.profile_service import get_profile

ZERO = Decimal("0.00")

# Cuántas categorías se muestran antes de agrupar el resto en "otros".
TOP_CATEGORIES = 5


def to_profile_input(profile: UserProfile) -> ProfileInput:
    return ProfileInput(
        current_balance=profile.current_balance,
        next_income_amount=profile.next_income_amount,
        next_income_date=profile.next_income_date,
        protected_amount=profile.protected_amount,
        safety_buffer=profile.safety_buffer,
    )


def to_commitment_inputs(commitments: list[Commitment]) -> list[CommitmentInput]:
    return [
        CommitmentInput(
            amount=c.amount,
            due_date=c.due_date,
            status=c.status.value,
            is_recurring=c.is_recurring,
        )
        for c in commitments
    ]


def month_bounds(day: date) -> tuple[date, date]:
    """Primer y último día del mes de `day`."""
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _expenses_total(session: Session, user_id: UUID, start: date, end: date) -> Decimal:
    """Total de gastos del usuario en el rango, resuelto por SQL (una sola consulta)."""
    total = session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    ).scalar_one()
    return Decimal(total).quantize(ZERO)


def _month_totals_by_category(
    session: Session, user_id: UUID, start: date, end: date
) -> tuple[Decimal, dict[str, Decimal]]:
    """Ingresos del mes y gastos del mes por categoría, en UNA consulta agrupada.

    El agrupamiento lo hace PostgreSQL (`GROUP BY type, category`): no se traen los
    movimientos a Python ni se consulta una vez por categoría.
    """
    rows = session.execute(
        select(Transaction.type, Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Transaction.type, Transaction.category)
    ).all()

    income_total = ZERO
    expenses: dict[str, Decimal] = {}
    for tx_type, category, total in rows:
        amount = Decimal(total).quantize(ZERO)
        if tx_type is TransactionType.EXPENSE:
            expenses[category] = expenses.get(category, ZERO) + amount
        else:
            income_total += amount
    return income_total, expenses


def build_category_summary(expenses: dict[str, Decimal]) -> list[dict[str, Any]]:
    """Top 5 categorías + el resto agrupado en "otros", de mayor a menor, con porcentaje.

    Sin gastos devuelve una lista vacía: no se inventan categorías en cero. Los porcentajes
    se calculan en Decimal sobre el total del período y se redondean a un decimal.
    """
    total = sum(expenses.values(), ZERO)
    if total <= ZERO:
        return []

    ranked = sorted(expenses.items(), key=lambda item: (-item[1], item[0]))
    top = dict(ranked[:TOP_CATEGORIES])
    rest = sum((amount for _, amount in ranked[TOP_CATEGORIES:]), ZERO)
    if rest > ZERO:
        top[OTHER_CATEGORY] = top.get(OTHER_CATEGORY, ZERO) + rest

    return [
        {
            "category": category,
            "amount": amount,
            "percentage": (amount * 100 / total).quantize(Decimal("0.1"), ROUND_HALF_UP),
        }
        for category, amount in sorted(top.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_month_activity(session: Session, user_id: UUID, today: date) -> dict[str, Any]:
    """Ingresos, gastos, ahorro y gasto por categoría del mes en curso.

    El período es el mes calendario de `today`, el mismo que usa la proyección de fin de
    mes del dashboard. Solo lectura y solo del usuario autenticado.
    """
    start, end = month_bounds(today)
    income_total, expenses = _month_totals_by_category(session, user_id, start, end)
    expenses_total = sum(expenses.values(), ZERO)

    previous_end = start - timedelta(days=1)
    previous_start, _ = month_bounds(previous_end)

    return {
        "month_income_total": income_total,
        "month_expenses_total": expenses_total,
        "month_savings": income_total - expenses_total,
        "previous_month_expenses_total": _expenses_total(
            session, user_id, previous_start, previous_end
        ),
        "category_summary": build_category_summary(expenses),
    }


def build_dashboard_summary(
    session: Session, user_id: UUID, as_of: date | None = None
) -> dict[str, Any]:
    """Resumen financiero + proyección de fin de mes + actividad del mes del usuario.

    Lanza NotFoundError si el perfil no existe. Solo lectura: no escribe nada. El motor
    financiero no cambia: recibe el perfil y los compromisos de ESE usuario y calcula
    igual que siempre.
    """
    profile = get_profile(session, user_id)
    commitments = commitment_service.list_commitments(session, user_id)
    today = as_of or date.today()

    profile_input = to_profile_input(profile)
    commitment_inputs = to_commitment_inputs(commitments)

    summary = build_summary(profile_input, commitment_inputs, today)
    forecast = build_month_end_forecast(profile_input, commitment_inputs, today)
    activity = build_month_activity(session, user_id, today)

    return {**summary, "forecast": forecast, **activity}
