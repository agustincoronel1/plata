"""Ruteo de intención a plan de tools. Determinístico y acotado."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.ai.agent.brain import extract_amount
from app.ai.agent.schemas import AgentIntent
from app.services.categorizer import resolve_expense_category
from app.services.financial_engine import add_months

_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_COMMITMENT_HINTS = {
    "alquiler": ("alquiler", "vivienda"),
    "internet": ("internet", "servicios"),
    "obra social": ("obra social", "salud"),
    "colegio": ("colegio", "educación"),
}

# Días de la semana, para "el viernes" / "el próximo martes". El valor es el que devuelve
# `date.weekday()` (lunes = 0).
_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

# Cómo se dice "el mes que viene" en rioplatense.
_NEXT_MONTH = re.compile(r"\b(?:el\s+)?(?:mes\s+que\s+viene|proximo\s+mes|mes\s+proximo)\b")

# Verbos con los que arranca un pedido de agendar, y muletillas que no son parte del
# nombre. Se sacan del texto antes de quedarse con lo que nombra al compromiso.
_COMMITMENT_VERBS = re.compile(
    r"\b(?:agenda|agendame|agendar|agrega|agregar|anota|anotar|carga|cargar|"
    r"registra|registrar|sumale|sumar|quiero|necesito|tengo\s+que\s+pagar|"
    r"recordame|record[aá]|pone|poner)\b"
)

# Palabras que rodean al nombre pero no lo forman.
_COMMITMENT_FILLER = re.compile(
    r"\b(?:un|una|el|la|los|las|mi|mis|de|del|para|por|que|se|paga|pago|pagar|"
    r"vence|vencia|compromiso|cuenta|factura|todos|cada|siguiente)\b"
)

# Marcas de recurrencia: "todos los meses", "mensual", "es recurrente".
_RECURRING = re.compile(
    r"\b(?:todos\s+los\s+meses|cada\s+mes|mensual(?:mente)?|recurrente|fij[oa])\b"
)

# Intenciones que implican una escritura (se pausan para aprobación).
WRITE_INTENTS = {AgentIntent.CREATE_TRANSACTION, AgentIntent.CREATE_COMMITMENT}


def plan_tools(
    intent: AgentIntent,
    message: str,
    args: dict[str, Any],
    as_of: date,
    last_simulation: dict[str, Any] | None,
    pending_commitment_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if args.get("_tool_calls"):
        return [
            {"name": call["name"], "arguments": call.get("arguments", {})}
            for call in args["_tool_calls"]
        ]

    if intent in (
        AgentIntent.DASHBOARD_SUMMARY,
        AgentIntent.EXPLAIN_AVAILABLE_MONEY,
        AgentIntent.DAILY_BUDGET,
    ):
        return [{"name": "get_financial_summary", "arguments": {}}]

    if intent == AgentIntent.LIST_COMMITMENTS:
        return [{"name": "list_pending_commitments", "arguments": {}}]

    if intent == AgentIntent.SEARCH_HISTORY:
        return [{"name": "search_transactions", "arguments": {"query": message}}]

    if intent == AgentIntent.ONE_TIME_PURCHASE:
        # Compra al contado: comprobación simple contra el disponible. Jamás el simulador
        # de cuotas (si no, la respuesta hablaría de "primera cuota" para un pago único).
        amount = args.get("amount")
        if amount is None:
            return []
        arguments: dict[str, Any] = {"amount": str(Decimal(str(amount)))}
        if args.get("category"):
            arguments["category"] = args["category"]
        return [{"name": "check_one_time_purchase", "arguments": arguments}]

    if intent == AgentIntent.SIMULATE_PURCHASE:
        amount, installments = _sim_params(args, last_simulation)
        if amount is None or installments is None:
            return []
        return [
            {
                "name": "simulate_purchase_preview",
                "arguments": {
                    "total_amount": str(amount),
                    "installments": installments,
                    "first_installment_date": as_of.isoformat(),
                },
            }
        ]

    if intent == AgentIntent.COMPARE_PURCHASE_DATES:
        amount, installments = _sim_params(args, last_simulation)
        if amount is None or installments is None:
            return []
        next_month = add_months(as_of, 1, as_of.day)
        return [
            {
                "name": "simulate_purchase_preview",
                "arguments": {
                    "total_amount": str(amount),
                    "installments": installments,
                    "first_installment_date": as_of.isoformat(),
                },
            },
            {
                "name": "simulate_purchase_preview",
                "arguments": {
                    "total_amount": str(amount),
                    "installments": installments,
                    "first_installment_date": next_month.isoformat(),
                },
            },
        ]

    if intent == AgentIntent.CREATE_TRANSACTION:
        return [{"name": "create_transaction_draft", "arguments": {"text": message}}]

    if intent == AgentIntent.CREATE_COMMITMENT:
        params = _commitment_params(message, args, as_of, pending_commitment_fields)
        if params is None:
            return []
        return [{"name": "create_commitment_draft", "arguments": params}]

    return []


def _sim_params(
    args: dict[str, Any], last: dict[str, Any] | None
) -> tuple[Decimal | None, int | None]:
    amount = args.get("amount")
    installments = args.get("installments")
    if (amount is None or installments is None) and last:
        amount = amount if amount is not None else last.get("amount")
        installments = installments if installments is not None else last.get("installments")
    amount = Decimal(str(amount)) if amount is not None else None
    return amount, int(installments) if installments is not None else None


def _commitment_params(
    message: str,
    args: dict[str, Any],
    as_of: date,
    pending: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    fields = extract_commitment_fields(message, args, as_of, pending)
    if fields["missing_fields"]:
        return None
    return {
        "name": fields["name"],
        "amount": str(fields["amount"]),
        "due_date": fields["due_date"],
        "category": resolve_expense_category(fields.get("category"), fields["name"]),
        "is_recurring": bool(fields.get("is_recurring", False)),
    }


def commitment_fields_after_turn(
    message: str,
    args: dict[str, Any],
    as_of: date,
    pending: dict[str, Any] | None,
    *,
    completed: bool,
) -> dict[str, Any] | None:
    if completed:
        return None
    return extract_commitment_fields(message, args, as_of, pending)


def extract_commitment_fields(
    message: str,
    args: dict[str, Any],
    as_of: date,
    pending: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = message.lower()
    fields: dict[str, Any] = dict(pending or {})
    source = list(fields.get("source_messages") or [])
    source.append(message)

    amount = args.get("amount") or _extract_commitment_amount(normalized, as_of)
    if amount is not None:
        fields["amount"] = str(amount)

    due_date = _extract_due_date(normalized, as_of)
    if due_date is not None:
        fields["due_date"] = due_date.isoformat()

    name, category = _extract_commitment_name_category(normalized, as_of)
    if name:
        fields["name"] = name
    if category:
        fields["category"] = category

    # La recurrencia se acumula entre turnos: si se dijo "todos los meses" en cualquier
    # mensaje del alta, el compromiso queda recurrente aunque el turno que lo completa no
    # lo repita.
    if _extract_is_recurring(normalized):
        fields["is_recurring"] = True

    if fields.get("name"):
        fields["category"] = resolve_expense_category(fields.get("category"), fields["name"])
    missing = [field for field in ("name", "amount", "due_date") if fields.get(field) in (None, "")]
    fields["missing_fields"] = missing
    fields["source_messages"] = source[-6:]
    return fields


def _date_span(normalized: str, as_of: date) -> tuple[date, int, int] | None:
    """La fecha de vencimiento y el tramo de texto que la expresa.

    Devuelve el tramo además de la fecha porque quien extrae el monto necesita BORRARLO
    antes de buscar números: en "350000 para el 5 de septiembre" hay dos números y el día
    no es plata. Sin esto, "el 5" se leía como monto.

    Se reconocen, en orden de especificidad:

    1. "el 5 de septiembre" (día y mes explícitos).
    2. "el 5 del mes que viene" (día explícito, mes relativo).
    3. "hoy", "mañana", "pasado mañana".
    4. "en N días / semanas / meses".
    5. "el mes que viene" (sin día: cae el mismo día del mes siguiente).
    6. "el viernes", "el próximo martes" (próxima ocurrencia de ese día).

    `as_of` es el hoy de la zona de negocio (`APP_TIMEZONE`), no la del servidor.
    """
    # 1. Día y mes explícitos.
    match = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)", normalized)
    if match and _MONTHS.get(match.group(2)) is not None:
        resolved = _explicit_date(int(match.group(1)), _MONTHS[match.group(2)], as_of)
        if resolved is not None:
            return resolved, match.start(), match.end()

    # 2. "el 5 del mes que viene".
    match = re.search(
        r"(?:el\s+)?(\d{1,2})\s+del\s+(?:mes\s+que\s+viene|proximo\s+mes)", normalized
    )
    if match:
        target = add_months(as_of, 1, int(match.group(1)))
        return target, match.start(), match.end()

    # 3. Relativas de día.
    for pattern, days in (
        (r"\bpasado\s+ma[nñ]ana\b", 2),
        (r"\bma[nñ]ana\b", 1),
        (r"\bhoy\b", 0),
    ):
        match = re.search(pattern, normalized)
        if match:
            return as_of + timedelta(days=days), match.start(), match.end()

    # 4. "en N días / semanas / meses".
    match = re.search(r"\ben\s+(\d{1,3})\s+(dias?|semanas?|meses?|mes)\b", normalized)
    if match:
        cantidad = int(match.group(1))
        unidad = match.group(2)
        if unidad.startswith("dia"):
            target = as_of + timedelta(days=cantidad)
        elif unidad.startswith("semana"):
            target = as_of + timedelta(weeks=cantidad)
        else:
            target = add_months(as_of, cantidad, as_of.day)
        return target, match.start(), match.end()

    # 5. "el mes que viene", sin día: mismo número de día, mes siguiente.
    match = _NEXT_MONTH.search(normalized)
    if match:
        return add_months(as_of, 1, as_of.day), match.start(), match.end()

    # 6. Día de la semana: la próxima vez que caiga.
    match = re.search(
        r"\b(?:el\s+|este\s+|proximo\s+|el\s+proximo\s+)?"
        r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
        normalized,
    )
    if match:
        target_weekday = _WEEKDAYS[match.group(1)]
        delta = (target_weekday - as_of.weekday()) % 7 or 7
        return as_of + timedelta(days=delta), match.start(), match.end()

    return None


def _explicit_date(day: int, month: int, as_of: date) -> date | None:
    """Fecha con día y mes dichos a mano.

    El salto al año siguiente se decide por MES, no por día, y es a propósito: Plata trata
    los compromisos vencidos como un caso normal (`overdue_commitments_amount`, y el motor
    financiero sigue descontando los `pending` con vencimiento pasado). Entonces "el 5 de
    agosto" dicho un 12 de agosto es una cuenta que se debe y todavía no se pagó, no un
    error: dejarla en el año en curso es lo correcto.

    Comparar por día llevaría a agendar el alquiler para dentro de doce meses, que es
    bastante peor que registrarlo vencido por una semana. En cambio "el 5 de enero" dicho
    en diciembre sí es del año que viene, y eso lo resuelve la comparación por mes.
    """
    year = as_of.year + (1 if month < as_of.month else 0)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_due_date(normalized: str, as_of: date) -> date | None:
    span = _date_span(normalized, as_of)
    return span[0] if span else None


def _extract_commitment_amount(normalized: str, as_of: date) -> Decimal | None:
    """Monto del compromiso, sin confundirlo con el día del vencimiento.

    Primero se recorta del texto el tramo que expresa la fecha; recién sobre lo que queda
    se busca el número. Así "350000 para el 5 de septiembre" da 350000 y no 5.

    Se acepta el número plano además de las unidades coloquiales (lucas, palos, mil): antes
    se exigía una unidad o el prefijo "son", y "el alquiler de 350000" no se detectaba.
    """
    span = _date_span(normalized, as_of)
    if span is not None:
        _, start, end = span
        normalized = normalized[:start] + " " + normalized[end:]

    # Un número suelto que sea claramente un año ("en 2027") no es un monto.
    without_years = re.sub(r"\b(?:19|20)\d{2}\b", " ", normalized)
    return extract_amount(without_years)


def _extract_commitment_name_category(
    normalized: str, as_of: date
) -> tuple[str | None, str | None]:
    """Nombre y categoría del compromiso.

    Los hints siguen primero porque dan una categoría curada ("obra social" -> salud). Si
    ninguno matchea, el nombre se deduce del texto en vez de rendirse: se le sacan el
    tramo de la fecha, el monto, los verbos de agendar y las muletillas, y lo que queda es
    el nombre. Antes solo existían los cuatro hints, así que "netflix", "la factura de luz"
    o "el gimnasio" nunca llegaban a crear nada.
    """
    for hint, result in _COMMITMENT_HINTS.items():
        if hint in normalized:
            return result

    text = normalized
    span = _date_span(text, as_of)
    if span is not None:
        _, start, end = span
        text = text[:start] + " " + text[end:]

    # Fuera el monto con su unidad, los conectores de precio y la puntuación.
    text = re.sub(r"\d[\d.,\s]*\s*(?:lucas?|palos?|mil(?:lones)?|millon(?:es)?)?", " ", text)
    # La recurrencia se saca ANTES que las muletillas: "todos los meses" se escribe con
    # palabras que el filtro de muletillas también borra ("todos", "los"), y si corriera
    # primero dejaría un "meses" suelto que terminaba pegado al nombre ("gimnasio meses").
    text = _RECURRING.sub(" ", text)
    text = re.sub(r"\b(?:son|vence|el|los|dia)\b", " ", text)
    text = _COMMITMENT_VERBS.sub(" ", text)
    text = _COMMITMENT_FILLER.sub(" ", text)
    text = re.sub(r"[^\wáéíóúñ\s]", " ", text)

    words = [word for word in text.split() if len(word) > 2]
    if not words:
        return None, None

    # Hasta cuatro palabras: alcanza para "tarjeta de credito" o "factura de luz" sin
    # arrastrar media frase.
    name = " ".join(words[:4])
    return name, None


def _extract_is_recurring(normalized: str) -> bool:
    return bool(_RECURRING.search(normalized))
