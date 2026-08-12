"""Servicio de registro de movimientos asistido por IA.

Independiente de FastAPI. Dos operaciones separadas:

- `parse_transaction`: interpreta texto y crea un BORRADOR. **No** toca PostgreSQL, **no**
  llama a `transaction_service`, **no** modifica el saldo.
- `confirm_transaction`: con confirmación humana, valida el movimiento (borrador +
  correcciones) y recién ahí reusa `transaction_service.create_transaction`. El saldo
  cambia una sola vez. Si PostgreSQL falla, el borrador queda `pending` para reintentar.
- `reject_transaction`: marca el borrador `rejected`. No toca la base.

El modelo propone; la persona dispone. La IA nunca crea el movimiento por sí sola.

`user_id` es keyword-only y obligatorio en las tres operaciones. Sale del JWT verificado:
el borrador se crea a nombre de esa persona y solo esa persona puede confirmarlo o
rechazarlo. Un borrador ajeno responde 404, igual que uno inexistente.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.exceptions import AIDraftValidationError
from app.ai.gateway import AIGateway
from app.ai.trace import log_parse_trace
from app.models.enums import TransactionType
from app.schemas.ai_transaction import (
    SUPPORTED_CURRENCY,
    AIIntent,
    ParsedTransactionDraft,
    TransactionConfirmationResponse,
    TransactionConfirmRequest,
    TransactionParseModelOutput,
    TransactionParseResponse,
)
from app.schemas.transaction import TransactionCreate, TransactionResponse
from app.services import transaction_service
from app.services.categorizer import resolve_expense_category
from app.services.draft_store import Draft, DraftStore

# Campos de un movimiento que pueden venir de la IA o corregirse a mano.
TX_FIELDS = ("type", "amount", "category", "description", "occurred_on", "payment_method")

# Estados de traza (no confundir con el status del dashboard).
STATUS_CONFIRMABLE = "confirmable"
STATUS_INCOMPLETE = "incomplete"
STATUS_UNKNOWN = "unknown"


def parse_transaction(
    gateway: AIGateway,
    store: DraftStore,
    source_text: str,
    as_of: date | None = None,
    *,
    user_id: uuid.UUID,
) -> TransactionParseResponse:
    """Interpreta `source_text`, aplica reglas de negocio y crea un borrador temporal.

    El borrador queda a nombre de `user_id`. No toca la base financiera ni el saldo.
    """
    today = as_of or date.today()
    trace_id = uuid.uuid4().hex

    result = gateway.parse_transaction(source_text=source_text, as_of=today, trace_id=trace_id)
    output = result.output

    transaction, missing, ambiguities, is_confirmable = _apply_business_rules(
        output, today, source_text
    )
    requires_confirmation = transaction is not None

    payload = {
        "intent": output.intent.value,
        "transaction": _draft_to_payload(transaction, source_text) if transaction else None,
        "confidence": str(output.confidence),
        "missing_fields": missing,
        "ambiguities": ambiguities,
        "explanation": output.explanation,
        "is_confirmable": is_confirmable,
    }
    draft = store.create(payload=payload, source_text=source_text, user_id=user_id)

    status = (
        STATUS_UNKNOWN
        if output.intent is AIIntent.UNKNOWN
        else (STATUS_CONFIRMABLE if is_confirmable else STATUS_INCOMPLETE)
    )
    log_parse_trace(
        trace_id=trace_id,
        draft_id=str(draft.draft_id),
        prompt_version=result.prompt_version,
        prompt_checksum=result.prompt_checksum,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        status=status,
        confidence=output.confidence,
        missing_fields_count=len(missing),
        ambiguities_count=len(ambiguities),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        source_text=source_text,
    )

    return TransactionParseResponse(
        draft_id=draft.draft_id,
        intent=output.intent,
        transaction=transaction,
        confidence=output.confidence,
        missing_fields=missing,
        ambiguities=ambiguities,
        explanation=output.explanation,
        requires_confirmation=requires_confirmation,
        is_confirmable=is_confirmable,
        prompt_version=result.prompt_version,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
    )


def confirm_transaction(
    session: Session,
    store: DraftStore,
    draft_id: uuid.UUID,
    request: TransactionConfirmRequest,
    *,
    user_id: uuid.UUID,
) -> TransactionConfirmationResponse:
    """Confirma un borrador y persiste el movimiento reusando `transaction_service`.

    Reclama el borrador de forma atómica (pending → confirming): dos confirmaciones
    simultáneas no pueden crear dos movimientos, la segunda recibe 409. Si algo falla
    después de reclamar (validación o PostgreSQL), el borrador vuelve a `pending` para
    reintentar. Recién con el movimiento creado pasa a `confirmed`.
    """
    if not request.confirmed:
        raise AIDraftValidationError(detail="Tenés que confirmar explícitamente el movimiento.")

    tx_store, same_transaction = _bind_store_to_session(store, session)
    # El reclamo atómico ya filtra por dueño: un borrador ajeno no matchea y es 404.
    draft = tx_store.claim_for_confirmation(draft_id, user_id=user_id)

    try:
        tx_payload, corrections, tx_create = _build_transaction_payload(draft, request)
        if same_transaction:
            transaction = transaction_service.create_transaction_no_commit(
                session, user_id, tx_create
            )
            tx_store.mark_confirmed(draft_id, user_id=user_id)
            session.commit()
            session.refresh(transaction)
        else:
            transaction = transaction_service.create_transaction(session, user_id, tx_create)
            tx_store.mark_confirmed(draft_id, user_id=user_id)
    except Exception:
        if session is not None:
            session.rollback()
        if not same_transaction:
            tx_store.release_to_pending(draft_id, user_id=user_id)
        raise

    corrected_fields = sorted(corrections.keys())
    ai_fields = sorted(
        field
        for field in TX_FIELDS
        if field not in corrections and tx_payload.get(field) is not None
    )

    return TransactionConfirmationResponse(
        draft_id=draft_id,
        transaction=TransactionResponse.model_validate(transaction),
        ai_fields=ai_fields,
        corrected_fields=corrected_fields,
        source_text=draft.source_text,
        created_at=transaction.created_at,
    )


def reject_transaction(store: DraftStore, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Marca el borrador propio como rechazado. No crea movimiento ni modifica el saldo."""
    store.mark_rejected(draft_id, user_id=user_id)


def _bind_store_to_session(store: DraftStore, session: Session | None) -> tuple[DraftStore, bool]:
    if session is None:
        return store, False
    binder = getattr(store, "with_session", None)
    if callable(binder):
        return binder(session), True
    return store, False


def _build_transaction_payload(
    draft: Draft, request: TransactionConfirmRequest
) -> tuple[dict[str, object], dict[str, object], TransactionCreate]:
    tx_payload = draft.payload.get("transaction")
    if tx_payload is None:
        raise AIDraftValidationError(
            detail="Este borrador no propone un movimiento. Interpretá el texto de nuevo."
        )

    corrections = request.corrections.model_dump(exclude_unset=True) if request.corrections else {}
    merged = {field: tx_payload.get(field) for field in TX_FIELDS}
    merged.update(corrections)

    try:
        tx_create = TransactionCreate.model_validate(merged)
    except ValidationError as exc:
        raise AIDraftValidationError(errors=_field_errors(exc)) from exc
    return tx_payload, corrections, tx_create


# --- Reglas de negocio sobre la salida del modelo ---


def _apply_business_rules(
    output: TransactionParseModelOutput, as_of: date, source_text: str = ""
) -> tuple[ParsedTransactionDraft | None, list[str], list[str], bool]:
    """Ajusta missing/ambiguities y decide si el borrador es confirmable sin correcciones."""
    if output.intent is AIIntent.UNKNOWN or output.transaction is None:
        return None, list(output.missing_fields), list(output.ambiguities), False

    tx = output.transaction
    missing = list(dict.fromkeys(output.missing_fields))
    ambiguities = list(dict.fromkeys(output.ambiguities))
    confirmable = True

    def add_missing(field: str) -> None:
        if field not in missing:
            missing.append(field)

    def add_ambiguity(text: str) -> None:
        if text not in ambiguities:
            ambiguities.append(text)

    if tx.currency != SUPPORTED_CURRENCY:
        add_ambiguity(
            f"Moneda no soportada ({tx.currency}): Vector solo maneja pesos argentinos (ARS)."
        )
        confirmable = False

    if tx.amount is None or tx.amount <= Decimal("0"):
        add_missing("amount")
        confirmable = False

    if tx.type is None:
        add_missing("type")
        confirmable = False

    # Categoría del gasto: la resuelven las reglas determinísticas, sin una segunda llamada
    # al modelo. Lo que el modelo haya propuesto se respeta si está en el vocabulario fijo y,
    # si no, entra como pista junto con la descripción y el texto original.
    if tx.type is TransactionType.EXPENSE:
        tx.category = resolve_expense_category(tx.category, tx.description, source_text)
        # Ya no falta: aunque el modelo la haya declarado faltante, el gasto tiene categoría.
        if "category" in missing:
            missing.remove("category")
    elif tx.category is None:
        add_missing("category")
        confirmable = False

    # Un gasto ya ocurrió: una fecha futura es sospechosa y bloquea la confirmación directa.
    if tx.type is TransactionType.EXPENSE and tx.occurred_on and tx.occurred_on > as_of:
        add_ambiguity("La fecha del gasto es futura; revisala antes de confirmar.")
        confirmable = False

    return tx, missing, ambiguities, confirmable


def _draft_to_payload(tx: ParsedTransactionDraft, source_text: str) -> dict[str, object]:
    """Serializa el borrador a JSON, con `source_text` fijado por el backend (autoritativo)."""
    return {
        "type": tx.type.value if tx.type else None,
        "amount": str(tx.amount) if tx.amount is not None else None,
        "category": tx.category,
        "description": tx.description,
        "occurred_on": tx.occurred_on.isoformat() if tx.occurred_on else None,
        "payment_method": tx.payment_method,
        "currency": tx.currency,
        "source_text": source_text,
    }


def _field_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Traduce un ValidationError a la forma detail de FastAPI, sin el valor crudo."""
    errors: list[dict[str, object]] = []
    for err in exc.errors():
        field = err["loc"][-1] if err.get("loc") else "body"
        errors.append({"loc": ["body", field], "msg": err.get("msg", "Valor inválido.")})
    return errors
