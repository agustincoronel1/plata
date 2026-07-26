"""Ruteo de intención a plan de tools. Determinístico y acotado."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.ai.agent.brain import extract_amount
from app.ai.agent.schemas import AgentIntent
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

    if intent in (AgentIntent.DASHBOARD_SUMMARY, AgentIntent.EXPLAIN_AVAILABLE_MONEY):
        return [{"name": "get_financial_summary", "arguments": {}}]

    if intent == AgentIntent.LIST_COMMITMENTS:
        return [{"name": "list_pending_commitments", "arguments": {}}]

    if intent == AgentIntent.SEARCH_HISTORY:
        return [{"name": "search_transactions", "arguments": {"query": message}}]

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
        "category": fields["category"],
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

    amount = args.get("amount") or _extract_commitment_amount(normalized)
    if amount is not None:
        fields["amount"] = str(amount)

    due_date = _extract_due_date(normalized, as_of)
    if due_date is not None:
        fields["due_date"] = due_date.isoformat()

    name, category = _extract_commitment_name_category(normalized)
    if name:
        fields["name"] = name
    if category:
        fields["category"] = category

    missing = [
        field
        for field in ("name", "amount", "due_date", "category")
        if fields.get(field) in (None, "")
    ]
    fields["missing_fields"] = missing
    fields["source_messages"] = source[-6:]
    return fields


def _extract_due_date(normalized: str, as_of: date) -> date | None:
    m = re.search(r"(?:vence|venc[eí]a|para)\s+el\s+(\d{1,2})\s+de\s+([a-záéíóú]+)", normalized)
    if not m:
        m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)", normalized)
    if not m:
        return None
    day = int(m.group(1))
    month = _MONTHS.get(m.group(2))
    if month is None:
        return None
    year = as_of.year + (1 if month < as_of.month else 0)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_commitment_amount(normalized: str) -> Decimal | None:
    if re.search(r"\d[\d.\s,]*\s*(lucas?|palos?|mil(?:lones)?|millon)", normalized):
        return extract_amount(normalized)
    if re.search(r"\bson\s+\d", normalized):
        return extract_amount(normalized)
    return None


def _extract_commitment_name_category(normalized: str) -> tuple[str | None, str | None]:
    for hint, result in _COMMITMENT_HINTS.items():
        if hint in normalized:
            return result
    return None, None
