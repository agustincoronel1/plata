"""Tests del servicio de registro asistido por IA: parse (reglas), confirm y reject."""

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai.exceptions import AIDraftValidationError
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.schemas.ai_transaction import (
    TransactionConfirmRequest,
    TransactionCorrections,
)
from app.services import ai_transaction_service as svc
from app.services import transaction_service
from app.services.draft_store import DraftStatus, InMemoryDraftStore
from tests.conftest import TEST_USER_ID, requires_postgres

AS_OF = date(2026, 7, 24)


def _gateway() -> AIGateway:
    return AIGateway(MockAIProvider())


def _parse(store: InMemoryDraftStore, text: str):
    return svc.parse_transaction(_gateway(), store, text, as_of=AS_OF, user_id=TEST_USER_ID)


def test_parse_confirmable() -> None:
    resp = _parse(InMemoryDraftStore(), "Gasté 25 lucas ayer en nafta con débito")
    assert resp.is_confirmable is True
    assert resp.requires_confirmation is True
    assert (
        resp.transaction.amount == pytest.approx(25000)
        or str(resp.transaction.amount) == "25000.00"
    )


def test_parse_sin_monto_no_es_confirmable() -> None:
    resp = _parse(InMemoryDraftStore(), "Gasté algo en el súper")
    assert resp.is_confirmable is False
    assert "amount" in resp.missing_fields


def test_parse_moneda_no_soportada() -> None:
    resp = _parse(InMemoryDraftStore(), "Pagué 30 dólares")
    assert resp.is_confirmable is False
    assert any("ARS" in a or "soportada" in a for a in resp.ambiguities)


def test_parse_unknown_no_requiere_confirmacion() -> None:
    resp = _parse(InMemoryDraftStore(), "Hola, ¿cómo estás?")
    assert resp.intent.value == "unknown"
    assert resp.requires_confirmation is False
    assert resp.transaction is None


def test_confirm_sin_confirmed_es_422() -> None:
    store = InMemoryDraftStore()
    resp = _parse(store, "Gasté 25 lucas ayer en nafta con débito")
    with pytest.raises(AIDraftValidationError):
        svc.confirm_transaction(
            None,
            store,
            resp.draft_id,
            TransactionConfirmRequest(confirmed=False),
            user_id=TEST_USER_ID,
        )


def test_confirm_fallo_db_devuelve_draft_a_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryDraftStore()
    resp = _parse(store, "Gasté 25 lucas ayer en nafta con débito")

    def boom(*_args, **_kwargs):
        raise RuntimeError("db caída")

    monkeypatch.setattr(transaction_service, "create_transaction", boom)
    with pytest.raises(RuntimeError):
        svc.confirm_transaction(
            None,
            store,
            resp.draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=TEST_USER_ID,
        )

    # El borrador vuelve a pending: se puede reintentar.
    assert store.get(resp.draft_id, user_id=TEST_USER_ID).status is DraftStatus.PENDING


def test_reject_marca_rechazado() -> None:
    store = InMemoryDraftStore()
    resp = _parse(store, "Gasté 25 lucas ayer en nafta con débito")
    svc.reject_transaction(store, resp.draft_id, user_id=TEST_USER_ID)
    assert store.get(resp.draft_id, user_id=TEST_USER_ID).status is DraftStatus.REJECTED


@requires_postgres
def test_confirm_crea_movimiento_y_actualiza_saldo(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    store = InMemoryDraftStore()
    resp = _parse(store, "Gasté 25 lucas ayer en nafta con débito")

    result = svc.confirm_transaction(
        db_session,
        store,
        resp.draft_id,
        TransactionConfirmRequest(confirmed=True),
        user_id=TEST_USER_ID,
    )
    assert str(result.transaction.amount) == "25000.00"
    assert result.transaction.category == "transporte"
    assert store.get(resp.draft_id, user_id=TEST_USER_ID).status is DraftStatus.CONFIRMED

    # Doble confirmación: el borrador ya está confirmado -> 409.
    from app.ai.exceptions import DraftAlreadyUsedError

    with pytest.raises(DraftAlreadyUsedError):
        svc.confirm_transaction(
            db_session,
            store,
            resp.draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=TEST_USER_ID,
        )


@requires_postgres
def test_confirm_con_correcciones(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    store = InMemoryDraftStore()
    # Sin pistas en el texto, el gasto queda en "otros"; la corrección humana manda.
    resp = _parse(store, "Gasté 5 lucas")
    assert resp.transaction.category == "otros"

    corrections = TransactionCorrections(
        category="ocio", occurred_on=date.today() - timedelta(days=1)
    )
    result = svc.confirm_transaction(
        db_session,
        store,
        resp.draft_id,
        TransactionConfirmRequest(confirmed=True, corrections=corrections),
        user_id=TEST_USER_ID,
    )
    assert result.transaction.category == "ocio"
    assert "category" in result.corrected_fields
