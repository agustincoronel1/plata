"""Ejecución determinística de las consultas que detectó `app.ai.fast_path`.

Traduce un `FastPathMatch` en una `ChatResponse` idéntica en forma a la del agente, sin
llamar al proveedor, sin embeddings, sin RAG y sin correr el grafo.

Dos reglas que ordenan el módulo:

1. **No se inventan fórmulas.** El disponible sale de `financial_engine.build_summary`, la
   misma función que alimenta el dashboard; el saldo sale del perfil, que es la fuente
   autoritativa. Los totales por período los suma `spending_service`, que es la misma casa
   que usa la tool del agente: la plata que se informa acá y la que informa el copiloto
   completo salen de la misma consulta. Acá no se recalcula nada que ya tenga dueño.
2. **Ante un dato que falta, se devuelve `None`.** Eso hace que quien llama siga por el
   camino normal (el agente), en lugar de responder algo distinto de lo que respondería
   hoy. Es la misma política de "ante la duda, LangGraph" del clasificador.

Las agregaciones nuevas son consultas chicas y resueltas por PostgreSQL (`SUM`, `LIMIT`):
nunca se traen los movimientos a Python para sumarlos.

El texto se arma con plantillas y el formato monetario es-AR que ya usa el copiloto
(`presentation.money`), así la respuesta no se distingue de las demás.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.agent.presentation import day_month, money
from app.ai.agent.schemas import AgentIntent, AnswerDetail, ChatResponse, StructuredAnswer
from app.ai.fast_path import FastPathIntent, FastPathMatch, Period
from app.core.timezone import app_today
from app.models import Commitment, CommitmentStatus, Transaction, TransactionType
from app.services.commitment_service import list_commitments
from app.services.dashboard_service import to_commitment_inputs, to_profile_input
from app.services.exceptions import NotFoundError
from app.services.financial_engine import ZERO, build_summary
from app.services.profile_service import get_profile
from app.services.spending_service import PERIOD_LABEL, period_bounds, sum_amount

# Cuántos elementos se muestran en las respuestas de lista. Corto a propósito: es un chat,
# no un listado; el detalle completo vive en las pantallas de movimientos y compromisos.
MAX_ITEMS = 5

# El fast path reusa las intenciones que ya existen en lugar de agregar valores nuevos al
# enum. No es cosmético: `AgentIntent` viaja dentro de `AgentPlanOutput`, que es el schema
# de structured outputs que se le manda al modelo, así que sumarle miembros cambiaría el
# contrato del agente. Reusando, el cuerpo de la respuesta queda idéntico al de siempre y
# el frontend no se entera; para distinguirlas está el campo `source`.
_AGENT_INTENT: dict[FastPathIntent, AgentIntent] = {
    # Los totales por período y categoría son `spending_summary`, la misma intención que
    # usa el agente para la tool determinística equivalente. Que las dos vías declaren lo
    # mismo es lo que permite que un seguimiento ("¿y el mes pasado?") entienda de qué se
    # venía hablando, sin importar quién contestó el turno anterior.
    FastPathIntent.EXPENSE_TOTAL: AgentIntent.SPENDING_SUMMARY,
    FastPathIntent.EXPENSE_BY_CATEGORY: AgentIntent.SPENDING_SUMMARY,
    FastPathIntent.INCOME_TOTAL: AgentIntent.SPENDING_SUMMARY,
    FastPathIntent.RECENT_TRANSACTIONS: AgentIntent.SEARCH_HISTORY,
    FastPathIntent.CURRENT_BALANCE: AgentIntent.DASHBOARD_SUMMARY,
    FastPathIntent.AVAILABLE_AMOUNT: AgentIntent.DASHBOARD_SUMMARY,
    FastPathIntent.PENDING_COMMITMENTS: AgentIntent.LIST_COMMITMENTS,
}

# ---------- Consultas ----------


def _recent(session: Session, user_id: uuid.UUID, *, expenses_only: bool) -> list[Transaction]:
    """Últimos movimientos del usuario, acotados por `LIMIT`.

    El orden es el mismo que el del listado de movimientos (fecha del movimiento y, a
    igualdad, fecha de alta). No se usa `transaction_service.list_transactions` porque esa
    trae el historial completo para después recortarlo en Python.
    """
    statement = select(Transaction).where(Transaction.user_id == user_id)
    if expenses_only:
        statement = statement.where(Transaction.type == TransactionType.EXPENSE)
    statement = statement.order_by(
        Transaction.occurred_on.desc(), Transaction.created_at.desc()
    ).limit(MAX_ITEMS)
    return list(session.execute(statement).scalars())


def _pending_commitments(session: Session, user_id: uuid.UUID) -> list[Commitment]:
    """Compromisos pendientes del usuario, del vencimiento más próximo al más lejano.

    Una sola consulta: el total y los primeros elementos salen de la misma lista. Los
    compromisos de una cuenta son pocos (decenas), así que no hay riesgo de traer de más.
    """
    return list(
        session.execute(
            select(Commitment)
            .where(
                Commitment.user_id == user_id,
                Commitment.status == CommitmentStatus.PENDING,
            )
            .order_by(Commitment.due_date.asc(), Commitment.created_at.asc())
        ).scalars()
    )


# ---------- Render ----------


def _label(transaction: Transaction) -> str:
    """Nombre visible de un movimiento: su descripción o, si no tiene, la categoría."""
    text = (transaction.description or transaction.category).strip()
    return text[:1].upper() + text[1:] if text else "Movimiento"


def _signed(transaction: Transaction) -> str:
    """Monto del movimiento. El ingreso lleva `+` para que una lista mixta se lea bien."""
    formatted = money(transaction.amount)
    return f"+{formatted}" if transaction.type is TransactionType.INCOME else formatted


def _render(headline: str, details: list[AnswerDetail]) -> str:
    """Texto final: el titular y, si hay, una lista breve con viñetas."""
    if not details:
        return headline
    lines = [f"• {d.label} — {d.value}" + (f" — vence {d.when}" if d.when else "") for d in details]
    return headline + "\n" + "\n".join(lines)


def _answer(
    headline: str,
    *,
    how: str,
    details: list[AnswerDetail] | None = None,
) -> tuple[str, StructuredAnswer]:
    items = details or []
    structured = StructuredAnswer(
        verdict="info", headline=headline, details=items, how_i_solved_it=how
    )
    return _render(headline, items), structured


# ---------- Resolución por intención ----------


def _resolve_total(
    session: Session, match: FastPathMatch, user_id: uuid.UUID, today: date
) -> tuple[str, StructuredAnswer]:
    period = match.period or Period.MONTH
    start, end = period_bounds(period, today)
    label = PERIOD_LABEL[period]
    is_income = match.intent is FastPathIntent.INCOME_TOTAL
    tx_type = TransactionType.INCOME if is_income else TransactionType.EXPENSE

    total = sum_amount(session, user_id, tx_type, start, end, match.category)
    how = (
        f"Sumé tus {'ingresos' if is_income else 'gastos'}"
        + (f" de {match.category}" if match.category else "")
        + f" entre el {day_month(start)} y el {day_month(end)}."
    )

    if total <= ZERO:
        if is_income:
            return _answer(f"No registraste ingresos {label}.", how=how)
        suffix = f" en {match.category}" if match.category else ""
        return _answer(f"No registraste gastos{suffix} {label}.", how=how)

    if is_income:
        # "Este mes ingresaron $X" suena mejor que "ingresaste": el dinero entra solo.
        return _answer(f"{label.capitalize()} ingresaron {money(total)}.", how=how)

    suffix = f" en {match.category}" if match.category else ""
    return _answer(f"Gastaste {money(total)}{suffix} {label}.", how=how)


def _resolve_balance(session: Session, user_id: uuid.UUID) -> tuple[str, StructuredAnswer]:
    profile = get_profile(session, user_id)
    return _answer(
        f"Tu saldo actual es de {money(profile.current_balance)}.",
        how="Es el saldo de tu perfil, ya actualizado con todos tus movimientos.",
    )


def _resolve_available(
    session: Session, user_id: uuid.UUID, today: date
) -> tuple[str, StructuredAnswer]:
    """Disponible real, con la fórmula del motor financiero. Acá no se calcula nada."""
    profile = get_profile(session, user_id)
    commitments = list_commitments(session, user_id)
    summary = build_summary(to_profile_input(profile), to_commitment_inputs(commitments), today)

    how = (
        f"Tomé tu saldo de {money(summary['current_balance'])} y resté "
        f"{money(summary['pending_commitments_amount'])} de compromisos, "
        f"{money(summary['protected_amount'])} protegidos y "
        f"{money(summary['safety_buffer'])} de colchón."
    )

    available = summary["available_real"]
    if available <= ZERO:
        return _answer(
            "No te queda margen disponible: entre tus compromisos, el dinero protegido y "
            f"tu colchón te faltan {money(summary['deficit_amount'])}.",
            how=how,
        )
    return _answer(
        f"Tenés {money(available)} disponibles después de contemplar tus compromisos, "
        "dinero protegido y colchón.",
        how=how,
    )


def _resolve_recent(
    session: Session, match: FastPathMatch, user_id: uuid.UUID
) -> tuple[str, StructuredAnswer]:
    transactions = _recent(session, user_id, expenses_only=match.expenses_only)
    noun = "gastos" if match.expenses_only else "movimientos"
    how = f"Traje tus últimos {len(transactions)} {noun} ordenados por fecha."

    if not transactions:
        return _answer(f"Todavía no registraste {noun}.", how=how)

    details = [AnswerDetail(label=_label(t), value=_signed(t)) for t in transactions]
    return _answer(f"Tus últimos {noun} fueron:", how=how, details=details)


def _resolve_commitments(
    session: Session, match: FastPathMatch, user_id: uuid.UUID
) -> tuple[str, StructuredAnswer]:
    commitments = _pending_commitments(session, user_id)
    total = sum((c.amount for c in commitments), ZERO)
    how = f"Sumé tus {len(commitments)} compromisos pendientes sin pagar."

    if not commitments:
        return _answer("No tenés compromisos pendientes.", how=how)

    if match.wants_total:
        return _answer(f"Tenés {money(total)} en compromisos pendientes.", how=how)

    shown = commitments[:MAX_ITEMS]
    details = [
        AnswerDetail(label=c.name.capitalize(), value=money(c.amount), when=day_month(c.due_date))
        for c in shown
    ]
    count = len(commitments)
    noun = "compromiso pendiente" if count == 1 else "compromisos pendientes"
    headline = f"Tenés {count} {noun} por {money(total)}:"
    return _answer(headline, how=how, details=details)


# ---------- Entrada pública ----------


def execute_fast_path(
    session: Session,
    match: FastPathMatch,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    as_of: date | None = None,
) -> ChatResponse | None:
    """Resuelve la consulta contra PostgreSQL y arma la respuesta del copiloto.

    Devuelve `None` cuando el dato base no está (una cuenta sin perfil todavía). En ese
    caso quien llama sigue por el flujo normal, que ya sabe qué contestar: el fast path no
    cambia lo que la persona ve, solo evita el modelo cuando puede.

    `user_id` es keyword-only y sin default, igual que en el resto de los servicios: todas
    las consultas de acá se filtran por el usuario del JWT y por ningún otro.
    """
    today = as_of or app_today()

    try:
        answer, structured = _dispatch(session, match, user_id, today)
    except NotFoundError:
        return None

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=uuid.uuid4(),
        answer=answer,
        structured_answer=structured,
        intent=_AGENT_INTENT[match.intent],
        # El fast path no usa tools, no recupera evidencia y nunca escribe: los tres
        # campos van vacíos y `requires_approval` en False, no por omisión sino porque
        # es literalmente lo que pasó.
        tools_used=[],
        evidence=[],
        assumptions=[],
        requires_approval=False,
        pending_action=None,
        trace_id=uuid.uuid4().hex,
        source="fast_path",
    )


def _dispatch(
    session: Session, match: FastPathMatch, user_id: uuid.UUID, today: date
) -> tuple[str, StructuredAnswer]:
    if match.intent is FastPathIntent.CURRENT_BALANCE:
        return _resolve_balance(session, user_id)
    if match.intent is FastPathIntent.AVAILABLE_AMOUNT:
        return _resolve_available(session, user_id, today)
    if match.intent is FastPathIntent.RECENT_TRANSACTIONS:
        return _resolve_recent(session, match, user_id)
    if match.intent is FastPathIntent.PENDING_COMMITMENTS:
        return _resolve_commitments(session, match, user_id)
    return _resolve_total(session, match, user_id, today)
