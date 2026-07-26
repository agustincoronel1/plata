"""Redacción grounded: arma el texto SOLO con números que ya vienen en los tool results.

El modelo no inventa montos: estas plantillas leen los resultados determinísticos de las
tools y la evidencia recuperada. Si un dato no está, no se afirma.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.ai.agent.schemas import AgentIntent


def _money(value: Any) -> str:
    if value is None:
        return "s/d"
    d = Decimal(str(value))
    entero = f"{d:,.0f}".replace(",", ".")
    return f"${entero}"


def _first_result(context: dict[str, Any], name: str) -> dict[str, Any] | None:
    for result in context.get("tool_results", []):
        if result.get("name") == name and result.get("ok"):
            return result.get("data")
    return None


def render_answer(intent: AgentIntent, context: dict[str, Any]) -> str:
    if intent in (AgentIntent.DASHBOARD_SUMMARY, AgentIntent.EXPLAIN_AVAILABLE_MONEY):
        data = _first_result(context, "get_financial_summary")
        if not data:
            return _safe()
        base = (
            f"Hoy podés gastar {_money(data['spendable_total'])} en total"
            f"{_daily(data)}. Tu saldo es {_money(data['current_balance'])} y ya desconté "
            f"compromisos ({_money(data['pending_commitments_amount'])}), "
            f"protegido ({_money(data['protected_amount'])}) y colchón "
            f"({_money(data['safety_buffer'])})."
        )
        return base

    if intent == AgentIntent.LIST_COMMITMENTS:
        data = _first_result(context, "list_pending_commitments")
        if not data:
            return _safe()
        items = data.get("commitments", [])
        if not items:
            return "No tenés compromisos pendientes antes de tu próximo ingreso."
        total = _money(data.get("total"))
        detalle = "; ".join(f"{c['name']} {_money(c['amount'])} ({c['due_date']})" for c in items)
        return f"Tenés {len(items)} pago(s) pendientes por {total}: {detalle}."

    if intent in (AgentIntent.SIMULATE_PURCHASE, AgentIntent.COMPARE_PURCHASE_DATES):
        results = [
            r["data"]
            for r in context.get("tool_results", [])
            if r.get("name") == "simulate_purchase_preview" and r.get("ok")
        ]
        if not results:
            return _safe()
        if intent == AgentIntent.COMPARE_PURCHASE_DATES and len(results) >= 2:
            a, b = results[0], results[1]
            return (
                f"Empezando en {a['first_installment_date']}: cuota "
                f"{_money(a['installment_amount'])}, {_verdict(a)}. "
                f"Empezando en {b['first_installment_date']}: cuota "
                f"{_money(b['installment_amount'])}, {_verdict(b)}."
            )
        a = results[0]
        return (
            f"{a['installments']} cuotas de {_money(a['installment_amount'])} desde "
            f"{a['first_installment_date']}: {_verdict(a)}."
        )

    if intent == AgentIntent.SEARCH_HISTORY:
        data = _first_result(context, "search_transactions")
        if not data:
            return _safe()
        n = data.get("count", 0)
        if n == 0 or data.get("not_enough_evidence"):
            return "No encontré evidencia suficiente para calcular ese total."
        return (
            f"Encontré {n} movimiento(s) por un total de {_money(data.get('total'))}. "
            "Te dejo la evidencia abajo."
        )

    if intent in (AgentIntent.CREATE_TRANSACTION, AgentIntent.CREATE_COMMITMENT):
        pending = context.get("pending_action")
        if not pending:
            if intent == AgentIntent.CREATE_COMMITMENT:
                partial = context.get("pending_commitment_fields") or {}
                missing = (
                    partial.get("missing_fields")
                    or context.get("planner_args", {}).get("missing_fields")
                    or []
                )
                if missing:
                    fields = ", ".join(missing)
                    return f"Me faltan estos datos del compromiso: {fields}. ¿Me los pasás?"
            return _safe()
        return (
            f"Preparé esto para registrar: {pending['summary']}. "
            "No guardé nada todavía: revisalo y aprobalo para que lo registre."
        )

    return _safe()


def _daily(data: dict[str, Any]) -> str:
    daily = data.get("daily_safe_to_spend")
    if daily is None or data.get("days_until_income") is None:
        return ""
    return f", o {_money(daily)} por día hasta tu próximo ingreso"


def _verdict(sim: dict[str, Any]) -> str:
    if sim.get("is_viable"):
        return "entra en tu presupuesto"
    return "quedás ajustado o en rojo, revisalo"


def _safe() -> str:
    return (
        "No pude resolver eso con datos confiables. Probá reformularlo o usá el formulario manual."
    )
