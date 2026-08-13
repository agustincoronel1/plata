"""Herramientas del copiloto: acotadas, con schema Pydantic y user_id forzado por backend.

El `user_id` vive en el ToolContext, que arma FastAPI a partir del JWT verificado y viaja
por `config["configurable"]` de LangGraph, fuera del estado y fuera del prompt. El modelo
no lo ve, no lo elige y no puede pasarlo como argumento: ningún schema de tool lo declara.

Las tools de lectura reusan los servicios determinísticos. Las de escritura NUNCA persisten:
crean un borrador (draft) que pausa el grafo hasta la aprobación humana. Ninguna tool expone
SQL, la Session, el saldo mutable, delete ni update arbitrario. Todo devuelve JSON serializable
y montos como Decimal/str (nunca float).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway
from app.ai.rag.retriever import HybridRetriever, SearchFilters, select_relevant, selected_total
from app.core.config import settings
from app.schemas.dashboard import DashboardSummaryResponse
from app.services import (
    ai_transaction_service,
    commitment_service,
    simulation_service,
    transaction_service,
)
from app.services.dashboard_service import (
    build_dashboard_summary,
    to_commitment_inputs,
    to_profile_input,
)
from app.services.draft_store import DraftStore
from app.services.financial_engine import ZERO, simulate_purchase, to_jsonable
from app.services.profile_service import get_profile


@dataclass
class ToolContext:
    """Contexto de ejecución de las tools. Lo arma el backend, nunca el modelo.

    `user_id` no tiene valor por defecto a propósito: sin default es imposible correr una
    tool sin decir de quién son los datos.
    """

    session: Session
    draft_store: DraftStore
    gateway: AIGateway
    as_of: date
    user_id: UUID


# --- Schemas de argumentos ---


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: date | None = None


class RecentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=5, ge=1, le=50)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    category: str | None = None
    tx_type: str | None = Field(default=None, pattern="^(income|expense)$")
    date_from: date | None = None
    date_to: date | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class SpendingSummaryArgs(BaseModel):
    """Total gastado o cobrado en un período. La agregación la hace PostgreSQL.

    Existe porque `search_transactions` NO sirve para totalizar: recupera los movimientos
    más parecidos a un texto (top-k acotado) y suma solo esos. Preguntar "cuánto gasté este
    mes" por ahí devolvía la suma de un puñado de movimientos como si fuera el total.
    """

    model_config = ConfigDict(extra="forbid")
    period: Literal["today", "week", "month", "previous_month"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    category: str | None = Field(default=None, max_length=60)
    tx_type: str | None = Field(default=None, pattern="^(income|expense)$")
    # Para "¿en qué categoría gasté más?": suma el período y además lo abre por categoría.
    breakdown: bool = False


class SimulateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    installments: int = Field(ge=1, le=120)
    first_installment_date: date | None = None


class OneTimePurchaseArgs(BaseModel):
    """Compra al contado: un solo pago hoy. No hay cuotas ni fecha de primera cuota."""

    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    category: str | None = Field(default=None, max_length=60)


class CreateTransactionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=1000)


class CreateCommitmentArgs(BaseModel):
    """Argumentos del alta de un compromiso, validados antes de tocar nada.

    `extra="forbid"` es lo que impide que el modelo cuele un campo de más —empezando por
    `user_id`, que sale del ToolContext y de ningún otro lado. El monto es Decimal y > 0
    con los mismos límites que la columna, y la fecha es un `date` real: una fecha
    imposible ("30 de febrero") muere acá y no en PostgreSQL.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    due_date: date
    category: str | None = Field(default=None, max_length=60)
    is_recurring: bool = False


def _fmt_money(value: Decimal) -> str:
    return "$" + f"{value:,.0f}".replace(",", ".")


# --- Implementaciones (reusan servicios determinísticos) ---


def _get_financial_summary(ctx: ToolContext, args: SummaryArgs) -> dict[str, Any]:
    data = build_dashboard_summary(ctx.session, ctx.user_id, args.as_of or ctx.as_of)
    return DashboardSummaryResponse.model_validate(data).model_dump(mode="json")


def _list_pending_commitments(ctx: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    items = [
        c
        for c in commitment_service.list_commitments(ctx.session, ctx.user_id)
        if c.status.value == "pending"
    ]
    total = sum((c.amount for c in items), Decimal("0"))
    return {
        "commitments": [
            {
                "id": str(c.id),
                "name": c.name,
                "amount": str(c.amount),
                "due_date": c.due_date.isoformat(),
                "category": c.category,
            }
            for c in items
        ],
        "total": str(total),
        "count": len(items),
    }


def _get_recent_transactions(ctx: ToolContext, args: RecentArgs) -> dict[str, Any]:
    txs = transaction_service.list_transactions(ctx.session, ctx.user_id)[: args.limit]
    return {
        "transactions": [
            {
                "id": str(t.id),
                "type": t.type.value,
                "amount": str(t.amount),
                "category": t.category,
                "description": t.description,
                "occurred_on": t.occurred_on.isoformat(),
            }
            for t in txs
        ]
    }


def _get_purchase_simulations(ctx: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    sims = simulation_service.list_purchase_simulations(ctx.session, ctx.user_id)
    return {
        "simulations": [
            {
                "id": str(s.id),
                "purchase_name": s.purchase_name,
                "total_amount": str(s.total_amount),
                "installments": s.installments,
                "installment_amount": str(s.installment_amount),
            }
            for s in sims
        ]
    }


def _simulate_purchase_preview(ctx: ToolContext, args: SimulateArgs) -> dict[str, Any]:
    profile = get_profile(ctx.session, ctx.user_id)
    commitments = commitment_service.list_commitments(ctx.session, ctx.user_id)
    first = args.first_installment_date or ctx.as_of
    result = simulate_purchase(
        to_profile_input(profile),
        to_commitment_inputs(commitments),
        args.total_amount,
        args.installments,
        first,
        ctx.as_of,
    )
    return {
        "installments": args.installments,
        "installment_amount": str(result["regular_installment_amount"]),
        "first_installment_date": first.isoformat(),
        "conclusion": result["conclusion"],
        "is_viable": result["conclusion"] == "fits_within_reserves",
        "risk_months_count": result["risk_months_count"],
        "minimum_margin": str(result["minimum_margin_with_purchase"]),
        "start_next_month": to_jsonable(result["start_next_month"]),
    }


def _check_one_time_purchase(ctx: ToolContext, args: OneTimePurchaseArgs) -> dict[str, Any]:
    """Compara una compra al contado contra el disponible seguro del motor financiero.

    NO simula cuotas: no hay calendario, ni primera cuota, ni meses de riesgo. El único
    cálculo propio es la resta `disponible - compra`; el disponible (que ya descuenta
    compromisos, dinero protegido y colchón) lo sigue calculando `build_summary`.
    """
    summary = build_dashboard_summary(ctx.session, ctx.user_id, ctx.as_of)
    data = DashboardSummaryResponse.model_validate(summary).model_dump(mode="json")
    spendable = Decimal(data["spendable_total"])
    remaining = spendable - args.amount
    fits = remaining >= 0
    return {
        "amount": str(args.amount),
        "category": args.category,
        "fits": fits,
        "spendable_total": data["spendable_total"],
        "remaining_after_purchase": str(remaining if fits else Decimal("0.00")),
        "over_budget_amount": str(ZERO if fits else -remaining),
        "current_balance": data["current_balance"],
        "pending_commitments_amount": data["pending_commitments_amount"],
        "protected_amount": data["protected_amount"],
        "safety_buffer": data["safety_buffer"],
        "days_until_income": data["days_until_income"],
        "next_income_date": data["next_income_date"],
        "daily_safe_to_spend": data["daily_safe_to_spend"],
    }


def _get_spending_summary(ctx: ToolContext, args: SpendingSummaryArgs) -> dict[str, Any]:
    """Total del período pedido, sumado por PostgreSQL sobre los movimientos del usuario."""
    from app.models import TransactionType
    from app.services.spending_service import Period, spending_summary

    return spending_summary(
        ctx.session,
        ctx.user_id,
        today=ctx.as_of,
        period=Period(args.period) if args.period else None,
        date_from=args.date_from,
        date_to=args.date_to,
        category=args.category,
        tx_type=TransactionType(args.tx_type) if args.tx_type else TransactionType.EXPENSE,
        breakdown=args.breakdown,
    )


def _search_transactions(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    tx_type = args.tx_type or _infer_tx_type(args.query)
    filters = SearchFilters(
        category=args.category,
        tx_type=tx_type,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    retriever = HybridRetriever(ctx.session)
    candidates = retriever.search(
        user_id=ctx.user_id, query=args.query, top_k=args.top_k, filters=filters
    )
    selected = select_relevant(
        candidates,
        vector_max_distance=settings.ai_rag_vector_max_distance,
        limit=settings.ai_rag_max_evidence,
    )
    ids = [c.transaction_id for c in selected if tx_type is None or c.tx_type == tx_type]
    agg = selected_total(ctx.session, ctx.user_id, ids, tx_type=tx_type)
    total, count = agg["total"], agg["count"]
    evidence = [
        {
            "evidence_id": str(c.transaction_id),
            "source_type": "transaction",
            "source_id": str(c.transaction_id),
            "title": f"{c.category} · ${c.amount}",
            "excerpt": c.searchable_text,
            "occurred_on": c.occurred_on.isoformat(),
            "amount": str(c.amount),
            "type": c.tx_type,
            "retrieval_method": "hybrid" if len(c.methods) > 1 else next(iter(c.methods), "hybrid"),
            "score": round(c.rrf_score, 6),
            "relevance_reasons": c.relevance_reasons,
        }
        for c in selected
    ]
    return {
        "count": count,
        "total": str(total),
        "evidence": evidence,
        "not_enough_evidence": not evidence,
    }


def _infer_tx_type(query: str) -> str | None:
    q = query.lower()
    if any(word in q for word in ("gasto", "gasté", "gaste", "pagué", "pague", "compré")):
        return "expense"
    if any(word in q for word in ("ingreso", "cobré", "cobre", "gané", "gane", "entró")):
        return "income"
    return None


def _create_transaction_draft(ctx: ToolContext, args: CreateTransactionArgs) -> dict[str, Any]:
    parsed = ai_transaction_service.parse_transaction(
        ctx.gateway, ctx.draft_store, args.text, as_of=ctx.as_of, user_id=ctx.user_id
    )
    tx = parsed.transaction
    summary = (
        f"{'ingreso' if tx and tx.type and tx.type.value == 'income' else 'gasto'} de "
        f"{_fmt_money(tx.amount)} en {tx.category}"
        if tx and tx.amount is not None
        else "movimiento incompleto"
    )
    return {
        "draft_id": str(parsed.draft_id),
        "kind": "create_transaction",
        "is_confirmable": parsed.is_confirmable,
        "summary": summary,
        "fields": parsed.transaction.model_dump(mode="json") if tx else None,
        "missing_fields": parsed.missing_fields,
    }


def _create_commitment_draft(ctx: ToolContext, args: CreateCommitmentArgs) -> dict[str, Any]:
    from app.services.categorizer import resolve_expense_category

    category = resolve_expense_category(args.category, args.name)
    payload = {
        "kind": "create_commitment",
        "fields": {
            "name": args.name,
            "amount": str(args.amount),
            "due_date": args.due_date.isoformat(),
            "category": category,
            "is_recurring": args.is_recurring,
        },
    }
    draft = ctx.draft_store.create(payload=payload, source_text=args.name, user_id=ctx.user_id)
    # El resumen es lo único que la persona lee antes de aprobar, así que dice TODO lo que
    # se va a escribir: monto, fecha, categoría y si se repite. Aprobar a ciegas un
    # compromiso recurrente y descubrirlo después sería peor que no tener la función.
    # La fecha se escribe como la lee una persona (05/09/2026), no en ISO: este resumen es
    # lo único que se revisa antes de aprobar, así que va en el idioma del producto.
    summary = (
        f"compromiso {args.name} de {_fmt_money(args.amount)} en {category} "
        f"para el {args.due_date.strftime('%d/%m/%Y')}"
    )
    if args.is_recurring:
        summary += " (todos los meses)"
    return {
        "draft_id": str(draft.draft_id),
        "kind": "create_commitment",
        "is_confirmable": True,
        "summary": summary,
        "fields": payload["fields"],
        "missing_fields": [],
    }


@dataclass(frozen=True)
class Tool:
    name: str
    args_model: type[BaseModel]
    fn: Any
    writes: bool


TOOLS: dict[str, Tool] = {
    "get_financial_summary": Tool(
        "get_financial_summary", SummaryArgs, _get_financial_summary, False
    ),
    "list_pending_commitments": Tool(
        "list_pending_commitments", EmptyArgs, _list_pending_commitments, False
    ),
    "get_recent_transactions": Tool(
        "get_recent_transactions", RecentArgs, _get_recent_transactions, False
    ),
    "get_purchase_simulations": Tool(
        "get_purchase_simulations", EmptyArgs, _get_purchase_simulations, False
    ),
    "simulate_purchase_preview": Tool(
        "simulate_purchase_preview", SimulateArgs, _simulate_purchase_preview, False
    ),
    "check_one_time_purchase": Tool(
        "check_one_time_purchase", OneTimePurchaseArgs, _check_one_time_purchase, False
    ),
    "search_transactions": Tool("search_transactions", SearchArgs, _search_transactions, False),
    "get_spending_summary": Tool(
        "get_spending_summary", SpendingSummaryArgs, _get_spending_summary, False
    ),
    "create_transaction_draft": Tool(
        "create_transaction_draft", CreateTransactionArgs, _create_transaction_draft, True
    ),
    "create_commitment_draft": Tool(
        "create_commitment_draft", CreateCommitmentArgs, _create_commitment_draft, True
    ),
}

MULTIPLE_SENSITIVE_ACTIONS_ERROR = "multiple_sensitive_actions_not_allowed"
MULTIPLE_SENSITIVE_ACTIONS_MESSAGE = "Vector prepara una acción sensible por vez."

INVENTED_AMOUNT_ERROR = "amount_not_stated_by_user"
INVENTED_AMOUNT_MESSAGE = (
    "No ejecuté el cálculo: ese monto no lo dijo la persona. Preguntale cuánto sale antes "
    "de volver a pedir esta herramienta."
)

# Tools que calculan sobre un monto que tiene que haber salido de la persona, y cuál es el
# argumento en cuestión. El resto de las tools leen datos propios y no reciben plata de
# afuera, así que no necesitan esta barrera.
AMOUNT_FROM_USER: dict[str, str] = {
    "simulate_purchase_preview": "total_amount",
    "check_one_time_purchase": "amount",
}


def is_write_tool(name: str) -> bool:
    return TOOLS.get(name).writes if name in TOOLS else False


def blocked_sensitive_tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "ok": False,
        "error": MULTIPLE_SENSITIVE_ACTIONS_ERROR,
        "message": MULTIPLE_SENSITIVE_ACTIONS_MESSAGE,
        "duration_ms": 0,
        "writes": is_write_tool(name),
        "data": None,
    }


def blocked_invented_amount_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "ok": False,
        "error": INVENTED_AMOUNT_ERROR,
        "message": INVENTED_AMOUNT_MESSAGE,
        "duration_ms": 0,
        "writes": is_write_tool(name),
        "data": None,
    }


def amount_is_from_user(
    name: str,
    arguments: dict[str, Any],
    user_texts: list[str],
    tool_results: list[dict[str, Any]] | None = None,
) -> bool:
    """Si el monto con el que se va a calcular salió de la persona (o de nuestros datos).

    Es la barrera que impide el peor final posible: que el modelo complete un precio que
    nadie dijo, lo pase al simulador y la respuesta salga con la pinta de estar respaldada,
    porque técnicamente el número *vuelve* de una tool. Un monto inventado que pasa por el
    motor financiero queda grounded para el verificador, y ahí ya no hay quién lo detecte.

    Se acepta lo que la persona escribió en la conversación y lo que devolvieron las tools
    de este turno (encadenar sobre el propio disponible no es inventar). Cualquier otra
    cosa se bloquea y el turno pasa a pedir el dato.
    """
    from app.ai.agent.brain import mentioned_amounts

    field = AMOUNT_FROM_USER.get(name)
    if field is None:
        return True
    raw = arguments.get(field)
    if raw is None:
        return True

    try:
        amount = int(Decimal(str(raw)))
    except (ArithmeticError, ValueError):
        return False

    if amount in mentioned_amounts(user_texts):
        return True

    from app.ai.agent.verifier import known_amounts

    return amount in known_amounts(tool_results or [], [])


def run_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Valida argumentos, ejecuta la tool y devuelve un registro uniforme con duración."""
    started = time.perf_counter()
    try:
        tool = TOOLS[name]
        args = tool.args_model.model_validate(arguments)
        data = tool.fn(ctx, args)
        ok = True
        error = None
    except Exception as exc:  # noqa: BLE001 - errores de tool son seguros y trazables
        data = None
        ok = False
        error = type(exc).__name__
    duration_ms = int((time.perf_counter() - started) * 1000)
    writes = TOOLS[name].writes if name in TOOLS else False
    return {
        "name": name,
        "arguments": arguments,
        "ok": ok,
        "error": error,
        "duration_ms": duration_ms,
        "writes": writes,
        "data": data,
    }
