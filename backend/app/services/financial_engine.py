"""Motor financiero determinístico de Plata.

Servicio **puro**: no depende de FastAPI, no toca la base, no hace commits, no modifica
modelos ni el saldo. Recibe datos y una fecha `as_of` (para poder probarlo con fechas
fijas) y devuelve resultados estructurados. Todo el dinero es `Decimal`; nunca `float`.

Regla fundamental: `current_balance` YA es el saldo actual después de los movimientos
históricos. El motor **parte de ese saldo**: no vuelve a sumar ingresos pasados ni a
restar gastos pasados, y no usa las transacciones para recalcularlo.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any

CENTS = Decimal("0.01")
# Cero con escala de 2 decimales: así todos los montos calculados (incluidos los que caen
# en cero, como un disponible sin compromisos) se serializan uniformemente como "0.00".
ZERO = Decimal("0.00")

# Estados posibles del resumen del dashboard.
STATUS_HEALTHY = "healthy"
STATUS_TIGHT = "tight"
STATUS_DEFICIT = "deficit"
STATUS_INCOMPLETE = "incomplete"

# Conclusiones neutrales de la simulación (nunca "comprá" / "no compres").
CONCLUSION_FITS = "fits_within_reserves"
CONCLUSION_BREAKS = "breaks_reserves"
CONCLUSION_INSUFFICIENT = "insufficient_data"

FORECAST_NOTE = (
    "Esta proyección utiliza únicamente ingresos y compromisos cargados. "
    "No estima gastos variables futuros."
)


# ---------- Entradas puras (independientes del ORM) ----------


@dataclass(frozen=True)
class ProfileInput:
    current_balance: Decimal
    next_income_amount: Decimal
    next_income_date: date | None
    protected_amount: Decimal
    safety_buffer: Decimal


@dataclass(frozen=True)
class CommitmentInput:
    amount: Decimal
    due_date: date
    status: str  # "pending" | "paid" | "cancelled"
    is_recurring: bool

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"


# ---------- Utilidades de fechas ----------


def month_end(year: int, month: int) -> date:
    """Último día del mes (maneja febrero y bisiestos vía calendar)."""
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(anchor: date, months: int, anchor_day: int | None = None) -> date:
    """Suma `months` meses conservando el día `anchor_day` cuando el mes lo permite.

    Si el mes destino no tiene ese día (31 → febrero, 31 → mes de 30), usa su último día.
    """
    day = anchor.day if anchor_day is None else anchor_day
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _ym(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _iter_months(start: tuple[int, int], end: tuple[int, int]):
    """Itera (año, mes) desde start hasta end inclusive."""
    year, month = start
    while (year, month) <= end:
        yield (year, month)
        month += 1
        if month > 12:
            month = 1
            year += 1


# ---------- Disponible real y monto diario ----------


def horizon_date(as_of: date, next_income_date: date | None) -> date:
    """Fecha hasta la que se mira para el disponible.

    Si hay próximo ingreso configurado y no está vencido, se usa esa fecha. Si no, el
    último día del mes de `as_of` (solo para el disponible total; el diario no es
    confiable en ese caso).
    """
    if next_income_date is not None and next_income_date >= as_of:
        return next_income_date
    return month_end(as_of.year, as_of.month)


def sum_pending_commitments(commitments: list[CommitmentInput], until: date) -> Decimal:
    """Suma de compromisos pending con due_date <= `until`.

    Incluye vencidos que siguen pending: un compromiso vencido todavía es dinero
    pendiente. Ignora paid y cancelled.
    """
    return sum(
        (c.amount for c in commitments if c.is_pending and c.due_date <= until),
        ZERO,
    )


def sum_overdue_commitments(commitments: list[CommitmentInput], as_of: date) -> Decimal:
    """Suma de compromisos pending ya vencidos (due_date < as_of)."""
    return sum(
        (c.amount for c in commitments if c.is_pending and c.due_date < as_of),
        ZERO,
    )


def build_summary(
    profile: ProfileInput, commitments: list[CommitmentInput], as_of: date
) -> dict[str, Any]:
    """Resumen financiero del dashboard. No modifica nada; solo calcula.

    Fórmulas:
        available_real = current_balance - pending_commitments - protected - safety
        spendable_total = max(available_real, 0)
        daily_safe_to_spend = spendable_total / days_until_income  (ROUND_DOWN, 2 dec.)
    """
    horizon = horizon_date(as_of, profile.next_income_date)
    pending_amount = sum_pending_commitments(commitments, horizon)
    overdue_amount = sum_overdue_commitments(commitments, as_of)

    available_real = (
        profile.current_balance - pending_amount - profile.protected_amount - profile.safety_buffer
    )
    spendable_total = available_real if available_real > ZERO else ZERO
    deficit_amount = -available_real if available_real < ZERO else ZERO

    days_until_income, daily_safe_to_spend = _days_and_daily(
        as_of, profile.next_income_date, spendable_total
    )

    warnings = _build_warnings(profile, as_of, overdue_amount, available_real)
    status = _build_status(profile, as_of, available_real)

    return {
        "as_of": as_of,
        "horizon_date": horizon,
        "current_balance": profile.current_balance,
        "pending_commitments_amount": pending_amount,
        "overdue_commitments_amount": overdue_amount,
        "protected_amount": profile.protected_amount,
        "safety_buffer": profile.safety_buffer,
        "available_real": available_real,
        "spendable_total": spendable_total,
        "deficit_amount": deficit_amount,
        "days_until_income": days_until_income,
        "daily_safe_to_spend": daily_safe_to_spend,
        "next_income_amount": profile.next_income_amount,
        "next_income_date": profile.next_income_date,
        "status": status,
        "warnings": warnings,
    }


def _days_and_daily(
    as_of: date, next_income_date: date | None, spendable_total: Decimal
) -> tuple[int | None, Decimal | None]:
    """Días hasta el ingreso y monto diario seguro (ROUND_DOWN, conservador)."""
    if next_income_date is None:
        return None, None
    if next_income_date < as_of:
        return 0, None
    if next_income_date == as_of:
        # Cobra hoy: todo lo disponible es para hoy.
        return 1, spendable_total
    days = (next_income_date - as_of).days
    daily = (spendable_total / days).quantize(CENTS, rounding=ROUND_DOWN)
    return days, daily


def _build_warnings(
    profile: ProfileInput, as_of: date, overdue_amount: Decimal, available_real: Decimal
) -> list[str]:
    warnings: list[str] = []
    if profile.next_income_date is None:
        warnings.append("No configuraste la fecha de tu próximo ingreso.")
    elif profile.next_income_date < as_of:
        warnings.append("La fecha de tu próximo ingreso ya pasó.")
    if overdue_amount > ZERO:
        warnings.append("Tenés compromisos pendientes vencidos.")
    if available_real < ZERO:
        warnings.append("Tus compromisos y reservas superan tu saldo actual.")
    return warnings


def _build_status(profile: ProfileInput, as_of: date, available_real: Decimal) -> str:
    if available_real < ZERO:
        return STATUS_DEFICIT
    if profile.next_income_date is None or profile.next_income_date < as_of:
        return STATUS_INCOMPLETE
    if available_real == ZERO:
        return STATUS_TIGHT
    return STATUS_HEALTHY


# ---------- Proyección de fin de mes ----------


def build_month_end_forecast(
    profile: ProfileInput, commitments: list[CommitmentInput], as_of: date
) -> dict[str, Any]:
    """Proyección a fin de mes con datos conocidos. No estima gastos variables.

    Las reservas (protected, safety) NO se restan del saldo proyectado, solo del margen.
    """
    end = month_end(as_of.year, as_of.month)

    income_before = ZERO
    nid = profile.next_income_date
    if nid is not None and as_of <= nid <= end:
        income_before = profile.next_income_amount

    commitments_before = sum_pending_commitments(commitments, end)

    projected_balance = profile.current_balance + income_before - commitments_before
    projected_margin = projected_balance - profile.protected_amount - profile.safety_buffer

    return {
        "month_end": end,
        "income_before_month_end": income_before,
        "commitments_before_month_end": commitments_before,
        "projected_month_end_balance": projected_balance,
        "projected_month_end_margin": projected_margin,
        "note": FORECAST_NOTE,
    }


# ---------- Calendario de cuotas ----------


def build_installment_schedule(
    total_amount: Decimal, installments: int, first_installment_date: date
) -> tuple[list[dict[str, Any]], Decimal]:
    """Calendario de cuotas. La suma es exactamente `total_amount`, sin perder centavos.

    Divide el total en cuotas regulares (ROUND_DOWN a 2 decimales) y ajusta el residuo de
    redondeo en la última cuota. Una fecha por mes, conservando el día de la primera cuota
    cuando el mes lo permite; si no, el último día del mes.

    Devuelve (schedule, cuota_regular).
    """
    regular = (total_amount / installments).quantize(CENTS, rounding=ROUND_DOWN)
    anchor_day = first_installment_date.day

    schedule: list[dict[str, Any]] = []
    accumulated = ZERO
    for index in range(installments):
        due = (
            first_installment_date
            if index == 0
            else add_months(first_installment_date, index, anchor_day)
        )
        # La última cuota absorbe el residuo para que la suma cuadre al centavo.
        amount = total_amount - accumulated if index == installments - 1 else regular
        accumulated += amount
        schedule.append({"number": index + 1, "due_date": due, "amount": amount})

    return schedule, regular


# ---------- Simulación mensual ----------


def _income_for_month(profile: ProfileInput, as_of: date, year: int, month: int) -> Decimal:
    """Ingreso recurrente proyectado para un mes (supuesto explícito de la simulación)."""
    nid = profile.next_income_date
    if nid is None or profile.next_income_amount <= ZERO:
        return ZERO
    day = min(nid.day, calendar.monthrange(year, month)[1])
    occurrence = date(year, month, day)
    # Empieza en el mes de next_income_date y solo cuenta ocurrencias futuras.
    if (year, month) >= _ym(nid) and occurrence >= as_of:
        return profile.next_income_amount
    return ZERO


def _commitments_for_month(
    commitments: list[CommitmentInput], as_of: date, year: int, month: int
) -> Decimal:
    """Compromisos proyectados para un mes.

    - pending no recurrente: una sola vez, en el mes de due_date; si está vencido, en el
      mes actual (primer mes de la simulación).
    - pending recurrente: cada mes desde su due_date.
    - paid y cancelled: no se proyectan.
    """
    as_of_ym = _ym(as_of)
    total = ZERO
    for c in commitments:
        if not c.is_pending:
            continue
        due_ym = _ym(c.due_date)
        if c.is_recurring:
            if (year, month) >= due_ym:
                total += c.amount
        else:
            charge_ym = due_ym if due_ym >= as_of_ym else as_of_ym
            if (year, month) == charge_ym:
                total += c.amount
    return total


def _run_scenario(
    profile: ProfileInput,
    commitments: list[CommitmentInput],
    as_of: date,
    schedule: list[dict[str, Any]],
    start: tuple[int, int],
    end: tuple[int, int],
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    """Corre la proyección mes a mes con y sin la compra en paralelo."""
    installments_by_month: dict[tuple[int, int], Decimal] = {}
    for inst in schedule:
        key = _ym(inst["due_date"])
        installments_by_month[key] = installments_by_month.get(key, ZERO) + inst["amount"]

    reserves = profile.protected_amount + profile.safety_buffer
    balance_without = profile.current_balance
    balance_with = profile.current_balance
    months: list[dict[str, Any]] = []

    for year, month in _iter_months(start, end):
        opening_without = balance_without
        opening_with = balance_with
        income = _income_for_month(profile, as_of, year, month)
        commitment = _commitments_for_month(commitments, as_of, year, month)
        installment = installments_by_month.get((year, month), ZERO)

        closing_without = opening_without + income - commitment
        closing_with = opening_with + income - commitment - installment
        margin_without = closing_without - reserves
        margin_with = closing_with - reserves

        months.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "opening_balance_without_purchase": opening_without,
                "income_amount": income,
                "commitment_amount": commitment,
                "closing_balance_without_purchase": closing_without,
                "purchase_installment_amount": installment,
                "closing_balance_with_purchase": closing_with,
                "margin_without_purchase": margin_without,
                "margin_with_purchase": margin_with,
                "breaks_reserve": margin_with < ZERO,
            }
        )
        balance_without = closing_without
        balance_with = closing_with

    return months, balance_without, balance_with


def _scenario_metrics(
    profile: ProfileInput,
    commitments: list[CommitmentInput],
    as_of: date,
    schedule: list[dict[str, Any]],
    start: tuple[int, int],
) -> dict[str, Any]:
    end = _ym(schedule[-1]["due_date"])
    months, final_without, final_with = _run_scenario(
        profile, commitments, as_of, schedule, start, end
    )
    risk_months = [m["month"] for m in months if m["margin_with_purchase"] < ZERO]
    minimum_margin = min(m["margin_with_purchase"] for m in months)
    return {
        "months": months,
        "risk_months": risk_months,
        "minimum_margin": minimum_margin,
        "final_balance_without_purchase": final_without,
        "final_balance_with_purchase": final_with,
    }


def _assumptions(profile: ProfileInput, income_projected: bool) -> list[str]:
    assumptions = [
        "El total ingresado es el costo final financiado; no se calculan intereses ni CFT.",
        "Los compromisos recurrentes pendientes se repiten cada mes desde su vencimiento.",
        "Las reservas (dinero protegido y margen de seguridad) no son gastos: solo se usan "
        "para medir el margen de cada mes.",
    ]
    if income_projected:
        assumptions.insert(
            0,
            "Se asume que tu próximo ingreso se repite todos los meses el mismo día.",
        )
    else:
        assumptions.insert(
            0,
            "No se proyectan ingresos: falta la fecha o el monto de tu próximo ingreso.",
        )
    return assumptions


def simulate_purchase(
    profile: ProfileInput,
    commitments: list[CommitmentInput],
    total_amount: Decimal,
    installments: int,
    first_installment_date: date,
    as_of: date,
) -> dict[str, Any]:
    """Simula el impacto de una compra en cuotas y la compara con empezar un mes después.

    No modifica nada. Devuelve un resultado estructurado con Decimal y date; usar
    `to_jsonable` antes de guardarlo o serializarlo.
    """
    start = _ym(as_of)

    schedule, regular = build_installment_schedule(
        total_amount, installments, first_installment_date
    )
    base = _scenario_metrics(profile, commitments, as_of, schedule, start)

    # Alternativa: mover la primera cuota exactamente un mes hacia adelante.
    alt_first = add_months(first_installment_date, 1, first_installment_date.day)
    alt_schedule, _ = build_installment_schedule(total_amount, installments, alt_first)
    alt = _scenario_metrics(profile, commitments, as_of, alt_schedule, start)

    income_projected = profile.next_income_date is not None and profile.next_income_amount > ZERO
    if not income_projected:
        conclusion = CONCLUSION_INSUFFICIENT
    elif base["risk_months"]:
        conclusion = CONCLUSION_BREAKS
    else:
        conclusion = CONCLUSION_FITS

    return {
        "total_purchase_amount": total_amount,
        "installment_count": installments,
        "regular_installment_amount": regular,
        "first_installment_date": first_installment_date,
        "last_installment_date": schedule[-1]["due_date"],
        "schedule": schedule,
        "months": base["months"],
        "risk_months": base["risk_months"],
        "risk_months_count": len(base["risk_months"]),
        "minimum_margin_with_purchase": base["minimum_margin"],
        "final_balance_without_purchase": base["final_balance_without_purchase"],
        "final_balance_with_purchase": base["final_balance_with_purchase"],
        "conclusion": conclusion,
        "assumptions": _assumptions(profile, income_projected),
        "start_next_month": {
            "first_installment_date": alt_first,
            "risk_months_count": len(alt["risk_months"]),
            "minimum_margin": alt["minimum_margin"],
            "final_balance": alt["final_balance_with_purchase"],
            # Mejora si el peor mes queda estrictamente mejor que empezando ahora.
            "improves_margin": alt["minimum_margin"] > base["minimum_margin"],
        },
    }


# ---------- Serialización a JSON ----------


def to_jsonable(value: Any) -> Any:
    """Convierte Decimal → string y date → ISO string, recursivamente.

    Deja el resultado listo para guardarse como JSONB o devolverse por la API sin que
    queden Decimal ni date sin serializar.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
