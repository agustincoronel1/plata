"""Ruteo de intención a plan de tools. Determinístico y acotado.

Dos responsabilidades:

1. **Qué ruta es** (`route_for`): si la respuesta depende de datos de la persona, si es una
   simulación, una escritura o charla. La ruta la decide la intención, no el texto.
2. **Qué falta para poder resolverla** (`plan`). Que falte un dato NO es un error ni un
   plan vacío: es un `Plan` con `missing_fields`, y con los slots ya completados guardados
   para el turno siguiente. Antes, "¿puedo comprar una notebook en 9 cuotas?" devolvía una
   lista de tools vacía, indistinguible de una falla, y terminaba en el mensaje de error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.ai.agent.brain import extract_amount
from app.ai.agent.schemas import AgentIntent, AgentRoute
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

# Qué clase de turno es cada intención. Lo que no está acá es charla: `route_for` devuelve
# CONVERSATIONAL, que es una ruta válida y no un descarte.
_ROUTES: dict[AgentIntent, AgentRoute] = {
    AgentIntent.DASHBOARD_SUMMARY: AgentRoute.DETERMINISTIC,
    AgentIntent.EXPLAIN_AVAILABLE_MONEY: AgentRoute.DETERMINISTIC,
    AgentIntent.DAILY_BUDGET: AgentRoute.DETERMINISTIC,
    AgentIntent.LIST_COMMITMENTS: AgentRoute.DETERMINISTIC,
    AgentIntent.SEARCH_HISTORY: AgentRoute.DETERMINISTIC,
    AgentIntent.SPENDING_SUMMARY: AgentRoute.DETERMINISTIC,
    # No pide tools: reusa lo que ya se calculó y verificó en el turno anterior.
    AgentIntent.EXPLAIN_LAST_ANSWER: AgentRoute.DETERMINISTIC,
    AgentIntent.ONE_TIME_PURCHASE: AgentRoute.SIMULATION,
    AgentIntent.SIMULATE_PURCHASE: AgentRoute.SIMULATION,
    AgentIntent.COMPARE_PURCHASE_DATES: AgentRoute.SIMULATION,
    AgentIntent.CREATE_TRANSACTION: AgentRoute.ACTION,
    AgentIntent.CREATE_COMMITMENT: AgentRoute.ACTION,
    AgentIntent.CONVERSATIONAL: AgentRoute.CONVERSATIONAL,
    AgentIntent.UNKNOWN: AgentRoute.UNSUPPORTED,
}

# Datos sin los cuales la intención no se puede resolver. El orden es el que se usa para
# preguntar, así "me falta el monto y la fecha" sale siempre en el mismo orden.
REQUIRED_SLOTS: dict[AgentIntent, tuple[str, ...]] = {
    AgentIntent.ONE_TIME_PURCHASE: ("amount",),
    AgentIntent.SIMULATE_PURCHASE: ("amount", "installments"),
    AgentIntent.COMPARE_PURCHASE_DATES: ("amount", "installments"),
    AgentIntent.CREATE_COMMITMENT: ("name", "amount", "due_date"),
}

# Slots que se conservan entre turnos aunque no sean obligatorios (dan naturalidad: poder
# decir "la notebook" en vez de "el producto").
_OPTIONAL_SLOTS: dict[AgentIntent, tuple[str, ...]] = {
    AgentIntent.ONE_TIME_PURCHASE: ("item", "category"),
    AgentIntent.SIMULATE_PURCHASE: ("item",),
    AgentIntent.COMPARE_PURCHASE_DATES: ("item",),
}


def route_for(intent: AgentIntent) -> AgentRoute:
    """Ruta de una intención. Lo desconocido es charla, no falla."""
    return _ROUTES.get(intent, AgentRoute.CONVERSATIONAL)


def needs_user_data(intent: AgentIntent) -> bool:
    """Si responder honestamente exige leer los datos de la persona.

    Es la regla que decide si se consulta SQL: "¿qué es un gasto fijo?" no la necesita,
    "¿cuánto gasto yo en gastos fijos?" sí. No se consulta la base por las dudas.
    """
    return route_for(intent) in (AgentRoute.DETERMINISTIC, AgentRoute.SIMULATION)


@dataclass(frozen=True)
class Plan:
    """Qué ejecutar este turno y qué quedó pendiente.

    `missing_fields` no vacío significa que hay que PREGUNTAR, no que algo salió mal.
    `slots` es lo que ya se sabe (de este turno y de los anteriores) y viaja al checkpoint
    para que el turno siguiente complete en vez de arrancar de cero.
    """

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    slots: dict[str, Any] | None = None

    @property
    def needs_clarification(self) -> bool:
        return bool(self.missing_fields) and not self.tool_calls


def plan(
    intent: AgentIntent,
    message: str,
    args: dict[str, Any],
    as_of: date,
    last_simulation: dict[str, Any] | None = None,
    pending: dict[str, Any] | None = None,
) -> Plan:
    """Plan del turno: tools a ejecutar, datos que faltan y slots acumulados.

    `pending` es el `pending_request` del turno anterior (misma forma que `Plan.slots`):
    con él, "1.200.000" a secas completa la simulación que quedó a medias.
    """
    if args.get("_tool_calls"):
        return Plan(
            tool_calls=[
                {"name": call["name"], "arguments": call.get("arguments", {})}
                for call in args["_tool_calls"]
            ]
        )

    if intent in REQUIRED_SLOTS:
        return _plan_with_slots(intent, message, args, as_of, last_simulation, pending)

    return Plan(tool_calls=plan_tools(intent, message, args, as_of, last_simulation))


def _plan_with_slots(
    intent: AgentIntent,
    message: str,
    args: dict[str, Any],
    as_of: date,
    last_simulation: dict[str, Any] | None,
    pending: dict[str, Any] | None,
) -> Plan:
    """Completa los datos de la intención con lo que ya se sabía y arma el plan."""
    previous = _previous_fields(intent, pending)

    if intent is AgentIntent.CREATE_COMMITMENT:
        fields = extract_commitment_fields(message, args, as_of, previous or None)
        missing = list(fields["missing_fields"])
    else:
        fields = _purchase_fields(intent, args, previous, last_simulation)
        missing = [name for name in REQUIRED_SLOTS[intent] if fields.get(name) in (None, "")]

    slots = {"intent": intent.value, "fields": fields, "missing_fields": missing}
    if missing:
        # Falta un dato: no se ejecuta nada y se recuerda lo que ya se sabe. El turno
        # siguiente trae el dato que falta y completa, sin repetir la pregunta entera.
        return Plan(missing_fields=missing, slots=slots)

    calls = _calls_for(intent, fields, as_of)
    return Plan(tool_calls=calls, slots=None if calls else slots)


def _previous_fields(intent: AgentIntent, pending: dict[str, Any] | None) -> dict[str, Any]:
    """Lo que ya se sabía, solo si venía de la MISMA intención.

    Si la persona cambió de tema, los slots viejos no se arrastran: preguntar el precio de
    una notebook y contestar sobre el alquiler no puede terminar simulando la notebook.
    """
    if not pending or pending.get("intent") != intent.value:
        return {}
    return dict(pending.get("fields") or {})


def _purchase_fields(
    intent: AgentIntent,
    args: dict[str, Any],
    previous: dict[str, Any],
    last_simulation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Monto, cuotas y producto de una compra, mezclando turno actual, slots y simulación.

    La precedencia es siempre la misma: lo que se dijo AHORA gana sobre lo que se sabía de
    antes, y lo de antes gana sobre la última simulación de la conversación.
    """
    fields = dict(previous)
    fallback = last_simulation or {}
    names = REQUIRED_SLOTS[intent] + _OPTIONAL_SLOTS.get(intent, ())
    for name in names:
        value = args.get(name)
        if value in (None, ""):
            value = fields.get(name)
        if value in (None, ""):
            value = fallback.get(name)
        if value not in (None, ""):
            fields[name] = value
    return fields


def _calls_for(intent: AgentIntent, fields: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    """Las tools de una intención cuyos datos ya están completos."""
    if intent is AgentIntent.CREATE_COMMITMENT:
        return [{"name": "create_commitment_draft", "arguments": _commitment_arguments(fields)}]

    if intent is AgentIntent.ONE_TIME_PURCHASE:
        arguments: dict[str, Any] = {"amount": str(Decimal(str(fields["amount"])))}
        if fields.get("category"):
            arguments["category"] = fields["category"]
        return [{"name": "check_one_time_purchase", "arguments": arguments}]

    amount = Decimal(str(fields["amount"]))
    installments = int(fields["installments"])
    dates = [as_of]
    if intent is AgentIntent.COMPARE_PURCHASE_DATES:
        dates.append(add_months(as_of, 1, as_of.day))
    return [
        {
            "name": "simulate_purchase_preview",
            "arguments": {
                "total_amount": str(amount),
                "installments": installments,
                "first_installment_date": first.isoformat(),
            },
        }
        for first in dates
    ]


def plan_tools(
    intent: AgentIntent,
    message: str,
    args: dict[str, Any],
    as_of: date,
    last_simulation: dict[str, Any] | None = None,
    pending_commitment_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Tools de las intenciones que no necesitan completar datos.

    Sigue existiendo con esta firma porque es la fachada que usan los evaluadores y los
    tests de selección de tools. Las intenciones con slots pasan por `plan`.
    """
    if args.get("_tool_calls"):
        return [
            {"name": call["name"], "arguments": call.get("arguments", {})}
            for call in args["_tool_calls"]
        ]

    if intent in REQUIRED_SLOTS:
        return plan(
            intent, message, args, as_of, last_simulation, _as_pending(pending_commitment_fields)
        ).tool_calls

    if intent == AgentIntent.SPENDING_SUMMARY:
        arguments = {
            key: args[key]
            for key in ("period", "category", "tx_type", "breakdown")
            if args.get(key)
        }
        return [{"name": "get_spending_summary", "arguments": arguments}]

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

    if intent == AgentIntent.CREATE_TRANSACTION:
        return [{"name": "create_transaction_draft", "arguments": {"text": message}}]

    return []


def _as_pending(fields: dict[str, Any] | None) -> dict[str, Any] | None:
    """Envuelve unos campos de compromiso sueltos en la forma de `pending_request`."""
    if not fields:
        return None
    return {
        "intent": AgentIntent.CREATE_COMMITMENT.value,
        "fields": fields,
        "missing_fields": list(fields.get("missing_fields") or []),
    }


def _commitment_arguments(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": fields["name"],
        "amount": str(fields["amount"]),
        "due_date": fields["due_date"],
        "category": resolve_expense_category(fields.get("category"), fields["name"]),
        "is_recurring": bool(fields.get("is_recurring", False)),
    }


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

    El salto al año siguiente se decide por MES, no por día, y es a propósito: Vector trata
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
