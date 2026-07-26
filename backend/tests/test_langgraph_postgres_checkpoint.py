from collections.abc import Callable
from datetime import date

from sqlalchemy.orm import Session

from app.ai.agent.graph import close_checkpointer, get_compiled_graph
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.core.config import settings
from app.services import ai_chat_service
from app.services.draft_store_pg import PostgresDraftStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres


def test_postgres_checkpoint_recupera_pausa_y_aprueba(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    previous = settings.ai_checkpoint_store
    settings.ai_checkpoint_store = "postgres"
    close_checkpointer()
    store = PostgresDraftStore(session=db_session)
    gateway = AIGateway(MockAIProvider())
    try:
        first = ai_chat_service.chat(
            db_session,
            "Gasté 25 lucas ayer en nafta con débito",
            as_of=date(2026, 7, 24),
            draft_store=store,
            gateway=gateway,
        )
        assert first.requires_approval is True
        conversation_id = first.conversation_id
        action_id = first.pending_action.action_id

        close_checkpointer()
        approved = ai_chat_service.resume(
            db_session,
            conversation_id,
            action_id,
            approve=True,
            as_of=date(2026, 7, 24),
            draft_store=store,
            gateway=gateway,
        )

        assert approved.requires_approval is False
        assert "Registré" in approved.answer
    finally:
        settings.ai_checkpoint_store = previous
        close_checkpointer()
        get_compiled_graph.cache_clear()


def test_postgres_checkpoint_conserva_compromiso_parcial(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    previous = settings.ai_checkpoint_store
    settings.ai_checkpoint_store = "postgres"
    close_checkpointer()
    store = PostgresDraftStore(session=db_session)
    gateway = AIGateway(MockAIProvider())
    try:
        first = ai_chat_service.chat(
            db_session,
            "Necesito pagar el alquiler el 5 de agosto",
            as_of=date(2026, 7, 24),
            draft_store=store,
            gateway=gateway,
        )
        assert first.requires_approval is False

        close_checkpointer()
        second = ai_chat_service.chat(
            db_session,
            "Son 350 mil",
            conversation_id=first.conversation_id,
            as_of=date(2026, 7, 24),
            draft_store=store,
            gateway=gateway,
        )

        assert second.requires_approval is True
        assert second.pending_action.kind == "create_commitment"
        assert second.pending_action.draft["amount"] == "350000"
    finally:
        settings.ai_checkpoint_store = previous
        close_checkpointer()
        get_compiled_graph.cache_clear()
