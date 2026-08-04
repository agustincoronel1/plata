"""Schemas del dashboard financiero (solo salida).

Los montos son `Decimal` en Python y se serializan como string en el JSON; nunca `float`.
Las fechas se devuelven como `date`. Estos schemas describen el contrato del endpoint
GET /api/v1/dashboard/summary, que calcula con el motor financiero determinístico.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Money

# Porcentaje sobre el total de gastos del mes: Decimal (nunca float), 0–100 con un decimal.
Percentage = Annotated[Decimal, Field(ge=0, le=100, max_digits=4, decimal_places=1)]

ZERO_MONEY = Decimal("0.00")

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


class CategorySummaryItem(BaseModel):
    """Cuánto se gastó en una categoría durante el mes, y qué parte del total representa."""

    model_config = ConfigDict(from_attributes=True)

    category: str
    amount: Money
    percentage: Percentage


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

    # --- Actividad del mes en curso (agregado, solo lectura) ---
    #
    # Campos agregados después del contrato original: todos tienen valor por defecto, así
    # que un consumidor que no los conoce sigue funcionando igual. `month_savings` puede ser
    # negativo (se gastó más de lo que entró). `category_summary` incluye SOLO gastos: las
    # cinco categorías con más gasto y el resto agrupado en "otros", de mayor a menor.
    month_income_total: Money = ZERO_MONEY
    month_expenses_total: Money = ZERO_MONEY
    month_savings: Money = ZERO_MONEY
    previous_month_expenses_total: Money = ZERO_MONEY
    category_summary: list[CategorySummaryItem] = Field(default_factory=list)
