"""Capa de presentación del copiloto: de resultado determinístico a lenguaje humano.

Acá no se calcula plata. Todos los montos salen tal cual de los tool results, que a su vez
salen del motor financiero; esta capa solo elige qué mostrar y cómo decirlo:

1. Formato es-AR: `$20.000`, `29/07`, `01/08/2026`. Nunca `20000.00` ni `2026-08-01`.
2. Traducción de los campos internos (`spendable_total`, `breaks_reserves`, `is_viable`…)
   a frases del usuario. Ningún snake_case, nombre de tool ni schema llega a la respuesta.
3. Selección: por intención se arma una `StructuredAnswer` corta —decisión, monto,
   explicación breve y como mucho una recomendación— y se renderiza a texto.

La respuesta se construye desde estas plantillas, no desde texto libre del modelo: el
modelo elige intención y tools, pero no puede volcar el JSON de una tool.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.ai.agent.schemas import AgentIntent, AnswerDetail, StructuredAnswer

_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

SAFE_FALLBACK = (
    "No pude resolver eso con datos confiables. Probá reformularlo o usá el formulario manual."
)

# Nombre humano de cada campo que puede faltar en un compromiso (nunca se muestra la clave).
_FIELD_LABELS = {
    "name": "el nombre",
    "amount": "el monto",
    "due_date": "la fecha de vencimiento",
    "category": "la categoría",
}


# ---------- Formato ----------


def money(value: Any) -> str:
    """Monto en ARS con formato es-AR y sin centavos: `$20.000`, `-$3.000`.

    El peso no usa centavos en la práctica y los ejemplos del producto son enteros; la
    precisión real vive en el backend (Decimal). Trunca hacia cero, así nunca se muestra
    un peso que no existe.
    """
    if value is None:
        return "s/d"
    amount = int(Decimal(str(value)))
    sign = "-" if amount < 0 else ""
    entero = f"{abs(amount):,}".replace(",", ".")
    return f"{sign}${entero}"


def money_abs(value: Any) -> str:
    """Igual que `money`, pero sin signo: para frases como 'quedarías $3.000 por debajo'."""
    if value is None:
        return "s/d"
    return money(abs(Decimal(str(value))))


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def day_month(value: Any) -> str:
    """Fecha corta para listas: `29/07`."""
    parsed = _as_date(value)
    return f"{parsed.day:02d}/{parsed.month:02d}" if parsed else "s/d"


def full_date(value: Any) -> str:
    """Fecha completa: `01/08/2026`."""
    parsed = _as_date(value)
    return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year}" if parsed else "s/d"


def spoken_date(value: Any) -> str:
    """Fecha hablada: `1 de agosto`."""
    parsed = _as_date(value)
    return f"{parsed.day} de {_MONTHS_ES[parsed.month - 1]}" if parsed else "s/d"


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


# ---------- Vista pública de un tool result ----------

_PUBLIC_TOOL_VIEWS: dict[str, tuple[tuple[str, str, str], ...]] = {
    # (clave interna, etiqueta pública, formateador)
    "get_financial_summary": (
        ("spendable_total", "disponible_para_gastar", "money"),
        ("daily_safe_to_spend", "limite_por_dia", "money"),
        ("days_until_income", "dias_hasta_cobrar", "raw"),
        ("next_income_date", "fecha_en_que_cobras", "date"),
        ("current_balance", "saldo_actual", "money"),
        ("pending_commitments_amount", "pagos_pendientes", "money"),
        ("protected_amount", "dinero_protegido", "money"),
        ("safety_buffer", "colchon_de_seguridad", "money"),
        ("deficit_amount", "cuanto_te_falta", "money"),
    ),
    "check_one_time_purchase": (
        ("amount", "monto_de_la_compra", "money"),
        ("fits", "entra_en_tu_disponible", "raw"),
        ("spendable_total", "limite_seguro", "money"),
        ("remaining_after_purchase", "te_quedaria", "money"),
        ("over_budget_amount", "cuanto_te_pasarias", "money"),
        ("days_until_income", "dias_hasta_cobrar", "raw"),
        ("next_income_date", "fecha_en_que_cobras", "date"),
    ),
    "simulate_purchase_preview": (
        ("installments", "cantidad_de_cuotas", "raw"),
        ("installment_amount", "valor_de_cada_cuota", "money"),
        ("first_installment_date", "primera_cuota", "date"),
        ("is_viable", "entra_en_tu_presupuesto", "raw"),
        ("risk_months_count", "meses_ajustados", "raw"),
        ("minimum_margin", "peor_margen", "money"),
    ),
    "list_pending_commitments": (
        ("total", "total_a_pagar", "money"),
        ("count", "cantidad_de_pagos", "raw"),
    ),
    "search_transactions": (
        ("count", "movimientos_encontrados", "raw"),
        ("total", "total", "money"),
    ),
}

_FORMATTERS = {"money": money, "date": full_date, "raw": lambda value: value}


def public_tool_output(rec: dict[str, Any]) -> dict[str, Any]:
    """Proyección de un tool result apta para mostrarse o mandarse al modelo.

    Selecciona solo los datos relevantes y ya formateados. Así el modelo no recibe el JSON
    crudo con `breaks_reserves` o `spendable_total` y no puede copiarlo a la respuesta.
    """
    data = rec.get("data")
    view: dict[str, Any] = {"ok": rec.get("ok", False)}
    if rec.get("message"):
        view["mensaje"] = rec["message"]
    if not isinstance(data, dict):
        return view

    fields = _PUBLIC_TOOL_VIEWS.get(rec.get("name", ""))
    if fields is None:
        return view | {"resumen": data.get("summary") or data.get("kind")}

    for key, label, kind in fields:
        if key in data and data[key] is not None:
            view[label] = _FORMATTERS[kind](data[key])
    if rec.get("name") == "list_pending_commitments":
        view["pagos"] = [
            {
                "nombre": c["name"],
                "monto": money(c["amount"]),
                "vence": day_month(c["due_date"]),
            }
            for c in data.get("commitments", [])
        ]
    return view


# ---------- Armado de la respuesta por intención ----------


def _first_result(context: dict[str, Any], name: str) -> dict[str, Any] | None:
    for result in context.get("tool_results", []):
        if result.get("name") == name and result.get("ok"):
            return result.get("data")
    return None


def _all_results(context: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        r["data"]
        for r in context.get("tool_results", [])
        if r.get("name") == name and r.get("ok") and r.get("data")
    ]


def _unavailable() -> StructuredAnswer:
    return StructuredAnswer(verdict="unavailable", headline=SAFE_FALLBACK)


def build_answer(intent: AgentIntent, context: dict[str, Any]) -> StructuredAnswer:
    """Arma la respuesta estructurada de la intención con los datos que efectivamente hay."""
    builder = _BUILDERS.get(intent)
    return builder(context) if builder else _unavailable()


def _how_from_summary(data: dict[str, Any]) -> str:
    """'Cómo lo resolví' en castellano: la cuenta contada, sin JSON ni nombres de campos."""
    return (
        f"Tomé tu saldo de {money(data['current_balance'])} y resté "
        f"{money(data['pending_commitments_amount'])} de compromisos, "
        f"{money(data['protected_amount'])} protegidos y "
        f"{money(data['safety_buffer'])} de colchón. El resultado es un disponible seguro "
        f"de {money(data['spendable_total'])}."
    )


def _build_spendable(context: dict[str, Any]) -> StructuredAnswer:
    data = _first_result(context, "get_financial_summary")
    if not data:
        return _unavailable()
    spendable = Decimal(str(data["spendable_total"]))
    how = _how_from_summary(data)

    if spendable <= 0:
        return StructuredAnswer(
            verdict="no",
            headline="Hoy no te queda margen para gastar.",
            explanation=(
                f"Entre tus próximos pagos, el dinero protegido y tu colchón de seguridad, "
                f"te faltan {money(data['deficit_amount'])}."
            ),
            recommendation="Te conviene esperar al próximo ingreso antes de gastar de más.",
            how_i_solved_it=how,
        )

    return StructuredAnswer(
        verdict="yes",
        headline=f"Podés gastar hasta {money(spendable)} hoy.",
        explanation=(
            "Ese monto ya contempla tus próximos pagos, el dinero protegido y tu colchón "
            "de seguridad."
        ),
        recommendation=_daily_hint(data),
        how_i_solved_it=how,
    )


def _daily_hint(data: dict[str, Any]) -> str | None:
    daily = data.get("daily_safe_to_spend")
    if daily is None:
        return None
    return f"Hasta que cobres, tu límite recomendado es de {money(daily)} por día."


def _build_explain(context: dict[str, Any]) -> StructuredAnswer:
    """Igual decisión que el disponible, pero la explicación ES el desglose."""
    answer = _build_spendable(context)
    if answer.verdict == "unavailable" or not answer.how_i_solved_it:
        return answer
    return answer.model_copy(update={"explanation": answer.how_i_solved_it})


def _build_daily_budget(context: dict[str, Any]) -> StructuredAnswer:
    data = _first_result(context, "get_financial_summary")
    if not data:
        return _unavailable()
    daily = data.get("daily_safe_to_spend")
    days = data.get("days_until_income")
    if daily is None or days is None:
        # Sin fecha de próximo ingreso el diario no es confiable: se dice, no se inventa.
        return StructuredAnswer(
            verdict="needs_input",
            headline="Todavía no puedo darte un límite por día.",
            explanation=(
                "Me falta la fecha de tu próximo ingreso. Cargala en tu perfil y te la calculo."
            ),
            how_i_solved_it=_how_from_summary(data),
        )

    hasta = data.get("next_income_date")
    headline = f"Podés gastar aproximadamente {money(daily)} por día"
    headline += f" hasta el {spoken_date(hasta)}." if hasta else "."
    return StructuredAnswer(
        verdict="info",
        headline=headline,
        explanation=(
            f"Tenés {money(data['spendable_total'])} disponibles y "
            f"{_plural(int(days), 'falta 1 día', f'faltan {int(days)} días')} para tu "
            "próximo ingreso."
        ),
        how_i_solved_it=_how_from_summary(data),
    )


def _build_commitments(context: dict[str, Any]) -> StructuredAnswer:
    data = _first_result(context, "list_pending_commitments")
    if not data:
        return _unavailable()
    items = data.get("commitments", [])
    if not items:
        return StructuredAnswer(
            verdict="info",
            headline="No tenés pagos pendientes antes de tu próximo ingreso.",
            how_i_solved_it="Revisé tus compromisos pendientes y no encontré ninguno abierto.",
        )
    return StructuredAnswer(
        verdict="info",
        headline=f"Tenés {money(data['total'])} en pagos antes de cobrar:",
        details=[
            AnswerDetail(
                label=c["name"].capitalize(),
                value=money(c["amount"]),
                when=day_month(c["due_date"]),
            )
            for c in items
        ],
        how_i_solved_it=(
            f"Sumé tus {len(items)} "
            f"{_plural(len(items), 'pago pendiente', 'pagos pendientes')} sin pagar."
        ),
    )


def _build_one_time_purchase(context: dict[str, Any]) -> StructuredAnswer:
    data = _first_result(context, "check_one_time_purchase")
    if not data:
        return _unavailable()
    amount = money(data["amount"])
    limit = money(data["spendable_total"])
    how = (
        f"Comparé los {amount} de la compra con tu disponible seguro de {limit}, que ya "
        "descuenta tus compromisos, el dinero protegido y tu colchón."
    )

    if data["fits"]:
        remaining = money(data["remaining_after_purchase"])
        return StructuredAnswer(
            verdict="yes",
            headline=f"Sí, podés gastar {amount} hoy.",
            explanation=(
                f"Después de la compra te quedarían {remaining} disponibles hasta cobrar."
            ),
            how_i_solved_it=how,
        )

    return StructuredAnswer(
        verdict="no",
        headline=f"No te conviene gastar {amount} hoy.",
        explanation=(
            f"Tu límite seguro es de {limit}. Si hacés la compra, quedarías "
            f"{money_abs(data['over_budget_amount'])} por debajo de tu colchón de seguridad."
        ),
        recommendation=f"Recomendación: gastá hasta {limit} o esperá al próximo ingreso.",
        how_i_solved_it=how,
    )


def _build_simulation(context: dict[str, Any]) -> StructuredAnswer:
    results = _all_results(context, "simulate_purchase_preview")
    if not results:
        return _unavailable()
    sim = results[0]
    count = int(sim["installments"])
    cuotas = f"{count} {_plural(count, 'cuota', 'cuotas')} de {money(sim['installment_amount'])}"
    how = (
        f"Proyecté mes a mes tu saldo, tus ingresos y tus compromisos con {cuotas} "
        f"a partir del {full_date(sim['first_installment_date'])}."
    )

    if sim["is_viable"]:
        return StructuredAnswer(
            verdict="yes",
            headline=f"Sí, podés comprarlo en {cuotas}.",
            explanation=(
                f"Empezando el {full_date(sim['first_installment_date'])} no quedás por "
                "debajo de tu colchón de seguridad en ningún mes."
            ),
            how_i_solved_it=how,
        )

    meses = int(sim.get("risk_months_count") or 0)
    explanation = "Con esas cuotas quedarías por debajo de tu colchón de seguridad"
    if meses:
        explanation += f" en {meses} {_plural(meses, 'mes', 'meses')}"
    explanation += f", hasta {money_abs(sim['minimum_margin'])} en el peor mes."
    return StructuredAnswer(
        verdict="no",
        headline=f"No te conviene comprarlo en {cuotas}.",
        explanation=explanation,
        recommendation=_later_start_hint(sim),
        how_i_solved_it=how,
    )


def _later_start_hint(sim: dict[str, Any]) -> str | None:
    later = sim.get("start_next_month") or {}
    if not later.get("improves_margin"):
        return None
    return (
        "Recomendación: empezá a pagar el "
        f"{full_date(later.get('first_installment_date'))} y te queda más margen."
    )


def _build_comparison(context: dict[str, Any]) -> StructuredAnswer:
    results = _all_results(context, "simulate_purchase_preview")
    if len(results) < 2:
        return _build_simulation(context)
    now, later = results[0], results[1]
    later_is_better = Decimal(str(later["minimum_margin"])) > Decimal(str(now["minimum_margin"]))
    better = later if later_is_better else now
    cuota = money(now["installment_amount"])
    return StructuredAnswer(
        verdict="info",
        headline=f"Te conviene empezar el {full_date(better['first_installment_date'])}.",
        explanation=(
            f"La cuota es de {cuota} en los dos casos. Empezando el "
            f"{full_date(now['first_installment_date'])} "
            f"{'entra en tu presupuesto' if now['is_viable'] else 'quedás ajustado'}; "
            f"empezando el {full_date(later['first_installment_date'])} "
            f"{'entra' if later['is_viable'] else 'seguís ajustado'}."
        ),
        how_i_solved_it=(
            "Proyecté los dos escenarios mes a mes y comparé cuánto margen te queda en el "
            "peor mes de cada uno."
        ),
    )


def _build_search(context: dict[str, Any]) -> StructuredAnswer:
    data = _first_result(context, "search_transactions")
    if not data:
        return _unavailable()
    count = int(data.get("count", 0))
    if count == 0 or data.get("not_enough_evidence"):
        return StructuredAnswer(
            verdict="info",
            headline="No encontré movimientos que respalden ese total.",
            explanation="Probá con otra descripción o un rango de fechas distinto.",
            how_i_solved_it="Busqué en tus movimientos por texto y por similitud y no hubo match.",
        )
    return StructuredAnswer(
        verdict="info",
        headline=(
            f"Encontré {count} {_plural(count, 'movimiento', 'movimientos')} por "
            f"{money(data['total'])}."
        ),
        explanation="Abajo te dejo cuáles son.",
        how_i_solved_it=(
            f"Busqué en tus movimientos por texto y por similitud, y sumé los {count} que "
            "coinciden."
        ),
    )


def _build_write(context: dict[str, Any]) -> StructuredAnswer:
    pending = context.get("pending_action")
    if pending:
        return StructuredAnswer(
            verdict="needs_input",
            headline=f"Preparé un {pending['summary']}.",
            explanation="Todavía no lo registré: revisalo y aprobalo para que lo guarde.",
            how_i_solved_it="Armé un borrador con lo que me dijiste. Nada se guarda sin tu OK.",
        )
    partial = context.get("pending_commitment_fields") or {}
    missing = partial.get("missing_fields") or context.get("planner_args", {}).get(
        "missing_fields", []
    )
    if missing:
        labels = [_FIELD_LABELS.get(field, field) for field in missing]
        return StructuredAnswer(
            verdict="needs_input",
            headline=f"Me falta {_join_es(labels)} del pago.",
            explanation="Pasámelo y lo dejo listo para que lo apruebes.",
        )
    return _unavailable()


def _join_es(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" y {items[-1]}"


_BUILDERS = {
    AgentIntent.DASHBOARD_SUMMARY: _build_spendable,
    AgentIntent.EXPLAIN_AVAILABLE_MONEY: _build_explain,
    AgentIntent.DAILY_BUDGET: _build_daily_budget,
    AgentIntent.LIST_COMMITMENTS: _build_commitments,
    AgentIntent.ONE_TIME_PURCHASE: _build_one_time_purchase,
    AgentIntent.SIMULATE_PURCHASE: _build_simulation,
    AgentIntent.COMPARE_PURCHASE_DATES: _build_comparison,
    AgentIntent.SEARCH_HISTORY: _build_search,
    AgentIntent.CREATE_TRANSACTION: _build_write,
    AgentIntent.CREATE_COMMITMENT: _build_write,
}


# ---------- Render a texto ----------


def render(answer: StructuredAnswer) -> str:
    """Texto final: decisión primero, detalle, explicación breve y una sola recomendación."""
    blocks: list[str] = [answer.headline]
    if answer.details:
        blocks.append(
            "\n".join(
                f"- {d.label}: {d.value}" + (f" — {d.when}" if d.when else "")
                for d in answer.details
            )
        )
    if answer.explanation:
        blocks.append(answer.explanation)
    if answer.recommendation:
        blocks.append(answer.recommendation)
    return "\n\n".join(blocks)


def build_text(intent: AgentIntent, context: dict[str, Any]) -> str:
    return render(build_answer(intent, context))


# ---------- Control de calidad del texto libre del modelo ----------

MAX_ANSWER_CHARS = 700
MAX_ANSWER_LINES = 10

# Cualquier palabra snake_case es un campo interno filtrado (spendable_total, is_viable…).
_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# Jerga técnica escrita con espacios, que el snake_case no atrapa.
_JARGON = (
    "minimum margin",
    "risk months",
    "safe to spend",
    "spendable",
    "current balance",
    "breaks reserves",
    "fits within reserves",
    "available real",
    "chain of thought",
)
# Montos crudos sin formatear: "$20000.00", "$1,200,000.00".
_RAW_MONEY = re.compile(r"\$\s?\d+(?:,\d{3})*\.\d{2}\b")


def internal_leaks(text: str) -> list[str]:
    """Motivos por los que un texto NO es mostrable. Vacío = apto."""
    lowered = text.lower()
    reasons = [f"campo interno expuesto: {m}" for m in dict.fromkeys(_SNAKE_CASE.findall(lowered))]
    reasons += [f"jerga técnica: {word}" for word in _JARGON if word in lowered]
    reasons += [f"monto sin formato es-AR: {m}" for m in dict.fromkeys(_RAW_MONEY.findall(text))]
    if len(text) > MAX_ANSWER_CHARS:
        reasons.append("respuesta demasiado larga")
    if len([line for line in text.splitlines() if line.strip()]) > MAX_ANSWER_LINES:
        reasons.append("respuesta con demasiadas líneas")
    if text.count("?") > 1:
        reasons.append("más de una pregunta al final")
    return reasons


def is_presentable(text: str) -> bool:
    return bool(text.strip()) and not internal_leaks(text)
