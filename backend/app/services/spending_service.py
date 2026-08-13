"""Totales de movimientos por período y categoría. Una sola casa, tres consumidores.

Acá vive todo lo que responde "cuánto se gastó (o se cobró) entre tal y tal fecha", que es
la pregunta más frecuente del copiloto:

- el atajo determinístico (`fast_path_service`), que la contesta sin IA;
- la tool `get_spending_summary`, que es como el agente accede al mismo dato;
- el vocabulario de períodos (`parse_period`), que antes vivía suelto en el clasificador.

Dos reglas:

1. **Suma PostgreSQL, no Python.** Una sentencia con `SUM` y filtro por dueño; nunca se
   traen los movimientos para sumarlos en memoria.
2. **El período se nombra, no se adivina.** Si el texto no dice cuál, quien llama decide el
   default (el mes en curso, que es el del producto). `parse_period` devuelve `None` en vez
   de suponer: por no distinguir "el mes pasado" del mes en curso, el copiloto contestaba
   el total equivocado sin avisar.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionType
from app.services.dashboard_service import month_bounds
from app.services.financial_engine import ZERO


class Period(StrEnum):
    """Rangos temporales soportados. Cualquier otro se resuelve con fechas explícitas."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    PREVIOUS_MONTH = "previous_month"


# Cómo se nombra cada período dentro de una frase.
PERIOD_LABEL: dict[Period, str] = {
    Period.TODAY: "hoy",
    Period.WEEK: "esta semana",
    Period.MONTH: "este mes",
    Period.PREVIOUS_MONTH: "el mes pasado",
}

# El mes anterior va PRIMERO: "el mes pasado" contiene "mes", y si se evaluara después
# quedaría clasificado como el mes en curso, que es justo el error que se está corrigiendo.
_PATTERNS: tuple[tuple[Period, re.Pattern[str]], ...] = (
    (
        Period.PREVIOUS_MONTH,
        re.compile(
            r"\b(?:el\s+)?mes\s+(?:pasado|anterior)\b|\bmes\s+que\s+paso\b"
            r"|\b(?:el\s+)?anterior\b|\bultimo\s+mes\b"
        ),
    ),
    (Period.TODAY, re.compile(r"\bhoy\b")),
    (Period.WEEK, re.compile(r"\b(?:esta\s+semana|en\s+la\s+semana|semanal)\b")),
    (Period.MONTH, re.compile(r"\b(?:este\s+mes|en\s+el\s+mes|del\s+mes|mensual)\b")),
)

# Las mismas expresiones juntas, para poder sacarlas del texto antes de buscar la categoría:
# en "cuánto gasté en el mes", el "en" es del período, no de una categoría.
PERIOD_PHRASES = re.compile("|".join(pattern.pattern for _, pattern in _PATTERNS))


def parse_period(normalized: str) -> Period | None:
    """El período nombrado en el texto, o `None` si no se nombró ninguno."""
    for period, pattern in _PATTERNS:
        if pattern.search(normalized):
            return period
    return None


def strip_period(normalized: str) -> str:
    """El texto sin las expresiones de período."""
    return PERIOD_PHRASES.sub(" ", normalized)


def period_bounds(period: Period, today: date) -> tuple[date, date]:
    """Primer y último día del período, en la zona de negocio de Vector.

    `today` lo provee quien llama con `app_today()`, así un gasto de las 22:00 de Argentina
    cuenta en su día y no en el siguiente (el servidor corre en UTC).

    La semana es de lunes a domingo, y el mes es el mes calendario que ya usa el dashboard
    (`month_bounds`), para que el total del chat y el de la pantalla coincidan.
    """
    if period is Period.TODAY:
        return today, today
    if period is Period.WEEK:
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    if period is Period.PREVIOUS_MONTH:
        first_of_month, _ = month_bounds(today)
        return month_bounds(first_of_month - timedelta(days=1))
    return month_bounds(today)


def sum_amount(
    session: Session,
    user_id: uuid.UUID,
    tx_type: TransactionType,
    start: date,
    end: date,
    category: str | None = None,
) -> Decimal:
    """Total de movimientos del usuario en el rango. Una consulta, agregada por PostgreSQL.

    Incluye los gastos autogenerados al pagar un compromiso: son `Transaction` de tipo
    expense como cualquier otra, y para la persona ese pago es plata que salió.
    """
    statement: Select[tuple[Any]] = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.user_id == user_id,
        Transaction.type == tx_type,
        Transaction.occurred_on >= start,
        Transaction.occurred_on <= end,
    )
    if category is not None:
        statement = statement.where(Transaction.category == category)
    return Decimal(session.execute(statement).scalar_one()).quantize(ZERO)


def category_totals(
    session: Session,
    user_id: uuid.UUID,
    tx_type: TransactionType,
    start: date,
    end: date,
    limit: int = 5,
) -> list[dict[str, str]]:
    """En qué se fue la plata, de mayor a menor. Un `GROUP BY`, no un bucle en Python.

    Responde "¿en qué categoría gasté más?" y "¿en qué se me está yendo la guita?", que
    antes terminaban en la búsqueda híbrida y devolvían un puñado de movimientos sueltos en
    lugar de un desglose.
    """
    rows = session.execute(
        select(Transaction.category, func.sum(Transaction.amount).label("total"))
        .where(
            Transaction.user_id == user_id,
            Transaction.type == tx_type,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(limit)
    ).all()
    return [
        {"category": category, "total": str(Decimal(total).quantize(ZERO))}
        for category, total in rows
    ]


def spending_summary(
    session: Session,
    user_id: uuid.UUID,
    *,
    today: date,
    period: Period | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    category: str | None = None,
    tx_type: TransactionType = TransactionType.EXPENSE,
    breakdown: bool = False,
) -> dict[str, Any]:
    """Total del período con su rango y su conteo. Es el dato crudo, sin redacción.

    El rango puede venir por período nombrado o por fechas explícitas; si no viene ninguno,
    el mes en curso, que es el default del producto.
    """
    if date_from is not None or date_to is not None:
        start = date_from or date_to
        end = date_to or date_from
    else:
        start, end = period_bounds(period or Period.MONTH, today)

    total = sum_amount(session, user_id, tx_type, start, end, category)
    count = session.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == tx_type,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
            *([Transaction.category == category] if category is not None else []),
        )
    ).scalar_one()

    summary: dict[str, Any] = {
        "total": str(total),
        "count": int(count),
        "category": category,
        "tx_type": tx_type.value,
        "period": (period or Period.MONTH).value if date_from is None and date_to is None else None,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
    }
    # El desglose solo tiene sentido si NO se filtró por una categoría: si ya se preguntó
    # por una, repetirla como "la categoría donde más gastaste" no dice nada.
    if breakdown and category is None:
        summary["by_category"] = category_totals(session, user_id, tx_type, start, end)
    return summary
