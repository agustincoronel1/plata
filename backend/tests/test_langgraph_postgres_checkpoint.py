from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.ai.agent.graph import close_checkpointer, get_compiled_graph
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.core.config import settings
from app.services import ai_chat_service
from app.services.draft_store_pg import PostgresDraftStore
from tests.conftest import TEST_USER_ID, requires_postgres

pytestmark = requires_postgres


@pytest.fixture(scope="session", autouse=True)
def checkpoint_schema_ready() -> None:
    """Aplica el esquema del checkpointer antes de que ningún test abra su transacción.

    `PostgresSaver.setup()` crea sus tablas con `CREATE INDEX CONCURRENTLY`, que espera a
    que terminen TODAS las transacciones abiertas, aunque sean sobre tablas ajenas. Contra
    una base recién creada, si esa primera aplicación cayera dentro de un test, esperaría a
    la transacción externa de `db_session`, que a su vez espera a que el test termine:
    bloqueo mutuo permanente.

    Este fixture es de alcance `session`, así que corre antes que el `db_session` de cada
    test y sobre una conexión propia, sin ninguna transacción de test abierta. Con el
    esquema ya aplicado, los `setup()` que corren dentro de los tests no encuentran
    migraciones pendientes y no ejecutan DDL. Es idempotente: repetirlo no aplica nada.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with PostgresSaver.from_conn_string(conn_string) as saver:
        saver.setup()


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
            user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
        )

        assert second.requires_approval is True
        assert second.pending_action.kind == "create_commitment"
        assert second.pending_action.draft["amount"] == "350000"
    finally:
        settings.ai_checkpoint_store = previous
        close_checkpointer()
        get_compiled_graph.cache_clear()
