"""Schemas del dashboard financiero (solo salida).

Los montos son `Decimal` en Python y se serializan como string en el JSON; nunca `float`.
Las fechas se devuelven como `date`. Estos schemas describen el contrato del endpoint
GET /api/v1/dashboard/summary, que calcula con el motor financiero determinístico.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Money

# Estados del resumen:
# - healthy:    disponible real > 0 y hay fecha de ingreso válida.
# - tight:      disponible real == 0.
# - deficit:    disponible real < 0.
# - incomplete: falta la fecha del próximo ingreso o está vencida.
DashboardStatus = Literal["healthy", "tight", "deficit", "incomplete"]


class MonthEndForecastResponse(BaseModel):
    """Proyección de fin de mes con datos conocidos. No estima gastos variables."""

    model_config = ConfigDict(from_attributes=True)

    month_end: date
    income_before_month_end: Money
    commitments_before_month_end: Money
    projected_month_end_balance: Money
    projected_month_end_margin: Money
    note: str


class DashboardSummaryResponse(BaseModel):
    """Resumen financiero del dashboard.

    Fórmulas:
        available_real  = current_balance - pending_commitments - protected - safety
        spendable_total = max(available_real, 0)
        daily_safe_to_spend = spendable_total / days_until_income  (ROUND_DOWN, 2 dec.)

    `available_real` puede ser negativo; `spendable_total` nunca lo es; `deficit_amount`
    es cuánto falta para cubrir compromisos y reservas.
    """

    model_config = ConfigDict(from_attributes=True)

    as_of: date
    horizon_date: date
    current_balance: Money
    pending_commitments_amount: Money
    overdue_commitments_amount: Money
    protected_amount: Money
    safety_buffer: Money
    available_real: Money
    spendable_total: Money
    deficit_amount: Money
    days_until_income: int | None
    daily_safe_to_spend: Money | None
    next_income_amount: Money
    next_income_date: date | None
    status: DashboardStatus
    warnings: list[str]
    forecast: MonthEndForecastResponse
