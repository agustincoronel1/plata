import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.ai.agent.answers import render_answer
from app.ai.agent.schemas import AgentIntent
from app.ai.exceptions import DraftAlreadyUsedError
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.core.database import SessionLocal
from app.models import Commitment, Transaction, UserProfile
from app.models.ai_draft import AIDraft
from app.schemas.ai_transaction import TransactionConfirmRequest
from app.services import ai_chat_service, ai_transaction_service, transaction_service
from app.services.ai_chat_service import _confirm_commitment
from app.services.draft_store import DraftStatus
from app.services.draft_store_pg import PostgresDraftStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres
TEST_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _cleanup() -> None:
    with SessionLocal() as session:
        profile = session.get(UserProfile, TEST_USER_ID)
        if profile is not None:
            session.delete(profile)
            session.flush()
        session.execute(delete(AIDraft).where(AIDraft.user_id == TEST_USER_ID))
        session.commit()


@pytest.fixture(autouse=True)
def isolated_user():
    """Aísla estos tests en su propio usuario, sin tocar el perfil demo.

    Ya no hace falta parchear ninguna constante: tanto los servicios financieros como los
    de IA reciben el `user_id` como argumento explícito. Este archivo pasa TEST_USER_ID en
    cada llamada y limpia solo lo suyo.
    """
    _cleanup()
    yield
    _cleanup()


def _reset() -> None:
    with SessionLocal() as session:
        session.execute(delete(AIDraft).where(AIDraft.user_id == TEST_USER_ID))
        session.add(
            UserProfile(
                id=TEST_USER_ID,
                name="Atomic Test",
                currency="ARS",
                current_balance=Decimal("100000.00"),
                next_income_amount=Decimal("0"),
                next_income_date=None,
                protected_amount=Decimal("0"),
                safety_buffer=Decimal("0"),
            )
        )
        session.commit()


def _stored_transactions(session) -> list[Transaction]:
    """Movimientos de TEST_USER_ID.

    Las assertions de atomicidad cuentan filas, así que deben filtrar por `user_id`: la
    base de desarrollo comparte tablas con el usuario demo y el resultado del test no
    puede depender de que las tablas estén vacías.
    """
    return list(
        session.execute(select(Transaction).where(Transaction.user_id == TEST_USER_ID))
        .scalars()
        .all()
    )


def _stored_commitments(session) -> list[Commitment]:
    """Compromisos de TEST_USER_ID, filtrados por el mismo motivo que los movimientos."""
    return list(
        session.execute(select(Commitment).where(Commitment.user_id == TEST_USER_ID))
        .scalars()
        .all()
    )


def _tx_payload() -> dict:
    return {
        "transaction": {
            "type": "expense",
            "amount": "25000.00",
            "category": "transporte",
            "description": "nafta",
            "occurred_on": "2026-07-24",
            "payment_method": "débito",
        }
    }


def _commitment_payload() -> dict:
    return {
        "kind": "create_commitment",
        "fields": {
            "name": "alquiler",
            "amount": "350000",
            "due_date": "2026-08-05",
            "category": "vivienda",
        },
    }


class PlannedBrain:
    def __init__(self, calls: list[dict] | None = None) -> None:
        self.calls = calls or [
            {
                "name": "create_transaction_draft",
                "arguments": {"text": "Gaste 25 lucas ayer en nafta con debito"},
            },
            {
                "name": "create_commitment_draft",
                "arguments": {
                    "name": "alquiler",
                    "amount": "350000",
                    "due_date": "2026-08-05",
                    "category": "vivienda",
                },
            },
        ]

    def classify(self, message: str, history: list[dict], context: dict | None = None) -> dict:
        return {
            "intent": AgentIntent.CREATE_TRANSACTION,
            "confidence": 0.9,
            "args": {"_tool_calls": self.calls},
        }

    def answer(self, intent: AgentIntent, context: dict) -> str:
        return render_answer(intent, context)


def test_confirmacion_concurrente_postgres_crea_una_transaccion() -> None:
    _reset()
    draft = PostgresDraftStore().create(_tx_payload(), "nafta", user_id=TEST_USER_ID)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                ai_transaction_service.confirm_transaction(
                    session,
                    PostgresDraftStore(),
                    draft.draft_id,
                    TransactionConfirmRequest(confirmed=True),
                    user_id=TEST_USER_ID,
                )
                results.append("ok")
            except DraftAlreadyUsedError:
                results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with SessionLocal() as session:
        count = _stored_transactions(session)
        stored = PostgresDraftStore().get(draft.draft_id, user_id=TEST_USER_ID)
    assert sorted(results) == ["conflict", "ok"]
    assert len(count) == 1
    assert stored.status is DraftStatus.CONFIRMED


def test_dos_escrituras_misma_ronda_crean_un_solo_draft_postgres() -> None:
    _reset()
    with SessionLocal() as session:
        response = ai_chat_service.chat(
            session,
            "dos escrituras",
            draft_store=PostgresDraftStore(session=session),
            gateway=AIGateway(MockAIProvider()),
            brain=PlannedBrain(),
            user_id=TEST_USER_ID,
        )
        drafts = (
            session.execute(select(AIDraft).where(AIDraft.user_id == TEST_USER_ID)).scalars().all()
        )

    assert response.requires_approval is True
    assert response.pending_action.kind == "create_transaction"
    assert len(drafts) == 1


def test_escritura_incompleta_y_segunda_escritura_crean_un_solo_draft_postgres() -> None:
    _reset()
    with SessionLocal() as session:
        response = ai_chat_service.chat(
            session,
            "dos escrituras con primera incompleta",
            draft_store=PostgresDraftStore(session=session),
            gateway=AIGateway(MockAIProvider()),
            brain=PlannedBrain(
                [
                    {
                        "name": "create_transaction_draft",
                        "arguments": {"text": "Gaste algo en el super"},
                    },
                    {
                        "name": "create_commitment_draft",
                        "arguments": {
                            "name": "alquiler",
                            "amount": "350000",
                            "due_date": "2026-08-05",
                            "category": "vivienda",
                        },
                    },
                ]
            ),
            user_id=TEST_USER_ID,
        )
        drafts = (
            session.execute(select(AIDraft).where(AIDraft.user_id == TEST_USER_ID)).scalars().all()
        )

    assert response.requires_approval is False
    assert response.pending_action is None
    assert len(drafts) == 1
    assert drafts[0].status == DraftStatus.PENDING.value


def test_rollback_despues_de_flush_financiero_permite_reintento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    draft = PostgresDraftStore().create(_tx_payload(), "nafta", user_id=TEST_USER_ID)
    original = transaction_service.create_transaction_no_commit

    def boom(session, user_id, payload):
        tx = original(session, user_id, payload)
        assert tx.id is not None
        raise RuntimeError("boom before commit")

    monkeypatch.setattr(transaction_service, "create_transaction_no_commit", boom)
    with SessionLocal() as session, pytest.raises(RuntimeError):
        ai_transaction_service.confirm_transaction(
            session,
            PostgresDraftStore(),
            draft.draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=TEST_USER_ID,
        )

    with SessionLocal() as session:
        assert _stored_transactions(session) == []
    assert (
        PostgresDraftStore().get(draft.draft_id, user_id=TEST_USER_ID).status is DraftStatus.PENDING
    )

    monkeypatch.setattr(transaction_service, "create_transaction_no_commit", original)
    with SessionLocal() as session:
        ai_transaction_service.confirm_transaction(
            session,
            PostgresDraftStore(),
            draft.draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=TEST_USER_ID,
        )
    with SessionLocal() as session:
        assert len(_stored_transactions(session)) == 1


class FailingConfirmStore(PostgresDraftStore):
    def with_session(self, session):
        return FailingConfirmStore(ttl_seconds=int(self._ttl.total_seconds()), session=session)

    def mark_confirmed(self, draft_id, *, user_id):
        raise RuntimeError("boom confirming")


def test_rollback_al_finalizar_no_duplica_movimiento() -> None:
    _reset()
    draft = PostgresDraftStore().create(_tx_payload(), "nafta", user_id=TEST_USER_ID)
    with SessionLocal() as session, pytest.raises(RuntimeError):
        ai_transaction_service.confirm_transaction(
            session,
            FailingConfirmStore(),
            draft.draft_id,
            TransactionConfirmRequest(confirmed=True),
            user_id=TEST_USER_ID,
        )

    with SessionLocal() as session:
        assert _stored_transactions(session) == []
    assert (
        PostgresDraftStore().get(draft.draft_id, user_id=TEST_USER_ID).status is DraftStatus.PENDING
    )


def test_commitment_rollback_y_reintento() -> None:
    _reset()
    draft = PostgresDraftStore().create(_commitment_payload(), "alquiler", user_id=TEST_USER_ID)
    # El doble del contexto del copiloto. `user_id` es el mismo usuario aislado que usa
    # el resto del archivo: el compromiso confirmado tiene que quedar en ese perfil.
    ctx = type(
        "Ctx",
        (),
        {
            "session": SessionLocal(),
            "draft_store": FailingConfirmStore(),
            "user_id": TEST_USER_ID,
        },
    )()
    try:
        with pytest.raises(RuntimeError):
            _confirm_commitment(ctx, draft.draft_id)
    finally:
        ctx.session.close()

    with SessionLocal() as session:
        assert _stored_commitments(session) == []
    assert (
        PostgresDraftStore().get(draft.draft_id, user_id=TEST_USER_ID).status is DraftStatus.PENDING
    )

    ctx = type(
        "Ctx",
        (),
        {
            "session": SessionLocal(),
            "draft_store": PostgresDraftStore(),
            "user_id": TEST_USER_ID,
        },
    )()
    try:
        _confirm_commitment(ctx, draft.draft_id)
    finally:
        ctx.session.close()
    with SessionLocal() as session:
        assert len(_stored_commitments(session)) == 1
