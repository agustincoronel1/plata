"""Tests del arnés del smoke real: preflight, presupuesto e instrumentación.

No hacen ninguna llamada real (el proveedor está falseado). Cubren justo lo que protege la
plata del usuario: que sin configuración completa no se llame a nadie, y que el contador de
llamadas corte antes de pasarse del límite.
"""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.agent import brain as brain_module
from app.ai.providers import openai as provider_module
from app.ai.rag import embeddings as embeddings_module
from app.schemas.transaction import TransactionCreate
from app.scripts import real_ai_smoke as smoke
from app.services import transaction_service
from app.services.draft_store import DraftStatus
from app.services.draft_store_pg import PostgresDraftStore
from tests.conftest import TEST_USER_ID, requires_postgres


@pytest.fixture
def real_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuración real completa, salvo lo que cada test rompa a propósito."""
    monkeypatch.setenv("RUN_REAL_AI_TESTS", "1")
    monkeypatch.setattr(smoke.settings, "ai_provider", "openai")
    monkeypatch.setattr(smoke.settings, "ai_model", "gpt-test")
    monkeypatch.setattr(smoke.settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(smoke.settings, "ai_checkpoint_store", "postgres")
    monkeypatch.setattr(smoke.settings, "ai_embedding_provider", "openai")
    monkeypatch.setattr(smoke.settings, "ai_embedding_model", "text-embedding-3-small")


def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita depender de PostgreSQL en los tests de configuración."""
    monkeypatch.setattr(smoke, "_check_database", lambda pre: None)


def test_sin_run_real_ai_tests_no_se_ejecuta_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_REAL_AI_TESTS", raising=False)
    _no_db(monkeypatch)

    errors = smoke._check_environment().errors

    assert any("RUN_REAL_AI_TESTS" in problem for problem in errors)


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        ("ai_api_key", "", "AI_API_KEY"),
        ("ai_provider", "mock", "AI_PROVIDER"),
        ("ai_model", "mock-transaction-parser-v1", "AI_MODEL"),
        ("ai_model", "   ", "AI_MODEL"),
        ("ai_checkpoint_store", "memory", "AI_CHECKPOINT_STORE"),
    ],
)
def test_preflight_nombra_la_variable_que_falta(
    real_config: None,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: str,
    expected: str,
) -> None:
    _no_db(monkeypatch)
    monkeypatch.setattr(smoke.settings, attribute, value)

    errors = smoke._check_environment().errors

    assert any(expected in problem for problem in errors), errors
    assert not any("sk-test" in problem for problem in errors)


def test_preflight_no_filtra_la_api_key(real_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_db(monkeypatch)
    monkeypatch.setattr(smoke.settings, "ai_provider", "mock")

    pre = smoke._check_environment()

    assert "sk-test" not in " ".join(pre.errors)


def test_embeddings_mock_saltean_el_rag_sin_romper(
    real_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_db(monkeypatch)
    monkeypatch.setattr(smoke.settings, "ai_embedding_provider", "mock")

    pre = smoke._check_environment()

    assert pre.errors == []
    assert pre.rag_skip_reason is not None


def test_la_suite_nunca_usa_un_proveedor_real() -> None:
    """Regresión: correr pytest no puede gastar plata, ni con la key real en backend/.env.

    Con AI_PROVIDER=openai configurado, sin el forzado del conftest los tests de
    RAG, chat e indexación dispararían llamadas facturadas.
    """
    from app.core.config import settings as live

    assert live.ai_provider == "mock"
    assert live.ai_embedding_provider == "mock"
    assert live.ai_api_key == ""
    assert live.ai_checkpoint_store == "memory"


def test_los_evaluadores_tampoco_usan_un_proveedor_real() -> None:
    import app.ai.evaluators._common  # noqa: F401 - importarlo fuerza el modo offline
    from app.core.config import settings as live

    assert live.ai_provider == "mock"
    assert live.ai_embedding_provider == "mock"


def test_presupuesto_corta_antes_de_pasarse() -> None:
    budget = smoke.CallBudget(2)

    budget.spend("responses.parse")
    budget.spend("responses.parse")

    assert budget.used == 2
    assert budget.reserve(1) is False
    with pytest.raises(smoke.BudgetExceededError):
        budget.spend("responses.parse")
    assert budget.used == 2  # la llamada bloqueada no se contabiliza como hecha


def test_env_int_ignora_valores_invalidos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_AI_MAX_CALLS", "no-es-un-numero")
    assert smoke._env_int("REAL_AI_MAX_CALLS", 12) == 12

    monkeypatch.setenv("REAL_AI_MAX_CALLS", "5")
    assert smoke._env_int("REAL_AI_MAX_CALLS", 12) == 5

    monkeypatch.delenv("REAL_AI_MAX_CALLS")
    assert smoke._env_int("REAL_AI_MAX_CALLS", 12) == 12


def test_instrumentacion_cuenta_llamadas_y_no_guarda_contenido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = SimpleNamespace(type="function_call", name="get_financial_summary", call_id="call_1")

    class FakeResponses:
        def parse(self, **kwargs):
            return SimpleNamespace(
                output=[call],
                output_parsed=None,
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr(
        brain_module, "_load_openai_sdk", lambda: (FakeClient, RuntimeError, TimeoutError)
    )
    monkeypatch.setattr(
        provider_module, "_load_sdk", lambda: (FakeClient, RuntimeError, TimeoutError)
    )
    monkeypatch.setattr(
        embeddings_module.OpenAIEmbeddingProvider, "embed", lambda self, text: [0.0]
    )

    budget = smoke.CallBudget(3)
    smoke._install_budget(budget)

    client_cls, _, _ = brain_module._load_openai_sdk()
    client_cls(api_key="sk-test").responses.parse(
        input=[{"type": "function_call_output", "call_id": "call_1", "output": "{}"}]
    )

    assert budget.used == 1
    record = budget.records[0]
    assert record.kind == "responses.parse"
    assert record.input_tokens == 11
    assert record.output_tokens == 7
    assert record.tool_calls == [{"name": "get_financial_summary", "call_id": "call_1"}]
    assert record.sent_call_ids == ["call_1"]
    # El registro guarda metadatos, nunca prompts ni argumentos.
    assert not hasattr(record, "arguments")
    assert not hasattr(record, "prompt")


def test_el_hilo_del_smoke_se_arma_igual_que_en_produccion() -> None:
    """Con el `conversation_id` pelado, el smoke leía un hilo vacío y limpiaba nada.

    `ai_chat_service` le antepone el dueño al hilo del checkpointer. Comprobar el estado con
    el id a secas devuelve un snapshot vacío —así que el verificador "pasaba" por no haber
    nada que mirar— y el `delete ... where thread_id = ...` del cleanup no borraba una fila.
    """
    from app.services.ai_chat_service import _thread_id

    conversation_id = uuid.uuid4()

    hilo = smoke._thread(conversation_id)

    assert hilo == _thread_id(smoke.DEMO_USER_ID, conversation_id)
    assert hilo != str(conversation_id)


def test_ningun_lugar_del_smoke_usa_el_conversation_id_como_hilo() -> None:
    """Los cuatro usos eran el mismo error copiado; que no vuelva por una quinta vía."""
    from pathlib import Path

    fuente = Path(smoke.__file__).read_text(encoding="utf-8")

    assert 'thread_id": str(' not in fuente
    assert "thread_id = any" in fuente, "el cleanup de checkpoints sigue existiendo"


def test_solo_el_rag_puede_quedar_skipped_sin_fallar() -> None:
    budget = smoke.CallBudget(12)
    run = smoke.Run(budget=budget, session=None, as_of=date(2026, 7, 26))
    run.rag_skipped_by_config = True
    run.record("1_parser_structured_output", smoke.PASS)
    run.record(smoke.RAG_SCENARIO, smoke.SKIPPED, "AI_EMBEDDING_PROVIDER no es 'openai'")

    assert smoke._exit_code(run, budget) == 0


def test_skip_por_presupuesto_devuelve_exit_code_distinto_de_cero() -> None:
    budget = smoke.CallBudget(4)
    run = smoke.Run(budget=budget, session=None, as_of=date(2026, 7, 26))
    run.record("1_parser_structured_output", smoke.PASS)
    run.record("6_rechazo", smoke.SKIPPED, "presupuesto insuficiente")

    assert smoke._exit_code(run, budget) == 1


def test_rag_skipped_por_presupuesto_tambien_falla() -> None:
    budget = smoke.CallBudget(4)
    run = smoke.Run(budget=budget, session=None, as_of=date(2026, 7, 26))
    run.rag_skipped_by_config = False  # no fue por configuración: fue por plata
    run.record(smoke.RAG_SCENARIO, smoke.SKIPPED, "presupuesto insuficiente")

    assert smoke._exit_code(run, budget) == 1


def test_fail_devuelve_exit_code_distinto_de_cero() -> None:
    budget = smoke.CallBudget(12)
    run = smoke.Run(budget=budget, session=None, as_of=date(2026, 7, 26))
    run.record("1_parser_structured_output", smoke.FAIL, "monto inesperado")

    assert smoke._exit_code(run, budget) == 1


def test_todo_pass_devuelve_cero() -> None:
    budget = smoke.CallBudget(12)
    run = smoke.Run(budget=budget, session=None, as_of=date(2026, 7, 26))
    for name in ("1_parser_structured_output", smoke.RAG_SCENARIO, "6_rechazo"):
        run.record(name, smoke.PASS)

    assert smoke._exit_code(run, budget) == 0


def test_el_default_de_reintentos_es_cero() -> None:
    # El tope cuenta requests reales: con retries automáticos dejaría de ser fiel al gasto.
    assert smoke.DEFAULT_MAX_RETRIES == 0


# --- Limpieza: la corrida no puede dejar rastros --------------------------------------


def _run_with(session: Session) -> smoke.Run:
    return smoke.Run(budget=smoke.CallBudget(12), session=session, as_of=date(2026, 7, 26))


def _count_drafts(session: Session, ids: list[uuid.UUID]) -> int:
    return session.execute(
        text("select count(*) from ai_drafts where id = any(cast(:ids as uuid[]))"),
        {"ids": [str(i) for i in ids]},
    ).scalar_one()


@requires_postgres
def test_cleanup_borra_drafts_pending_confirmed_y_rejected(db_session: Session) -> None:
    store = PostgresDraftStore(session=db_session)
    pending = store.create(
        payload={"kind": "create_transaction", "fields": {}}, source_text="a", user_id=TEST_USER_ID
    )
    confirmed = store.create(
        payload={"kind": "create_transaction", "fields": {}}, source_text="b", user_id=TEST_USER_ID
    )
    rejected = store.create(
        payload={"kind": "create_transaction", "fields": {}}, source_text="c", user_id=TEST_USER_ID
    )

    store.claim_for_confirmation(confirmed.draft_id, user_id=TEST_USER_ID)
    store.mark_confirmed(confirmed.draft_id, user_id=TEST_USER_ID)
    store.mark_rejected(rejected.draft_id, user_id=TEST_USER_ID)

    ids = [pending.draft_id, confirmed.draft_id, rejected.draft_id]
    assert _count_drafts(db_session, ids) == 3
    assert store.get(confirmed.draft_id, user_id=TEST_USER_ID).status is DraftStatus.CONFIRMED
    assert store.get(rejected.draft_id, user_id=TEST_USER_ID).status is DraftStatus.REJECTED

    run = _run_with(db_session)
    run.created_draft_ids.extend(ids)

    assert smoke._delete_drafts(run) == 3
    assert _count_drafts(db_session, ids) == 0


@requires_postgres
def test_cleanup_no_toca_drafts_ajenos(db_session: Session) -> None:
    store = PostgresDraftStore(session=db_session)
    mine = store.create(
        payload={"kind": "create_transaction", "fields": {}},
        source_text="mio",
        user_id=TEST_USER_ID,
    )
    previo = store.create(
        payload={"kind": "create_transaction", "fields": {}},
        source_text="ajeno",
        user_id=TEST_USER_ID,
    )

    run = _run_with(db_session)
    run.created_draft_ids.append(mine.draft_id)  # solo el de la corrida

    assert smoke._delete_drafts(run) == 1
    assert _count_drafts(db_session, [mine.draft_id]) == 0
    assert _count_drafts(db_session, [previo.draft_id]) == 1


@requires_postgres
def test_cleanup_borra_movimientos_documentos_y_restaura_saldo(
    db_session: Session, make_profile
) -> None:
    make_profile()
    before = smoke._balance(db_session)
    marker = f"{smoke.RUN_ID} veterinaria"

    tx = transaction_service.create_transaction(
        db_session,
        TEST_USER_ID,
        TransactionCreate(
            type="expense",
            amount=Decimal("48000.00"),
            category="mascotas",
            description=marker,
            occurred_on=date(2026, 7, 26),
            payment_method="debito",
        ),
    )
    assert smoke._balance(db_session) == before - Decimal("48000.00")
    docs = db_session.execute(
        text("select count(*) from transaction_search_documents where transaction_id = :tid"),
        {"tid": str(tx.id)},
    ).scalar_one()
    assert docs == 1

    run = _run_with(db_session)
    run.created_transaction_ids.append(tx.id)
    detail = smoke._cleanup(run)

    assert "movimientos_borrados=1" in detail
    assert "fallidos=0" in detail
    assert "restos=0" in detail
    assert "documentos_huerfanos=0" in detail
    assert smoke._balance(db_session) == before  # el saldo vuelve solo, vía el servicio
    remaining = db_session.execute(
        text("select count(*) from transaction_search_documents where transaction_id = :tid"),
        {"tid": str(tx.id)},
    ).scalar_one()
    assert remaining == 0


@requires_postgres
def test_cleanup_borra_los_checkpoints_de_la_corrida(db_session: Session) -> None:
    """Los hilos se insertan como los escribe producción: `<user_id>:<conversation_id>`.

    Antes este test los insertaba con el `conversation_id` pelado, que es justamente lo que
    el cleanup buscaba mal: los dos lados compartían el error y por eso pasaba en verde
    mientras en una corrida real no se borraba ningún checkpoint.
    """
    mine, ajena = uuid.uuid4(), uuid.uuid4()
    for conversation_id in (mine, ajena):
        db_session.execute(
            text(
                "insert into checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
                "values (:t, '', :c, '{}'::jsonb)"
            ),
            {"t": smoke._thread(conversation_id), "c": str(uuid.uuid4())},
        )
    db_session.flush()

    run = _run_with(db_session)
    run.conversation_ids.append(mine)

    assert smoke._delete_checkpoints(run) == 1
    remaining = (
        db_session.execute(
            text("select thread_id from checkpoints where thread_id = any(:t)"),
            {"t": [smoke._thread(mine), smoke._thread(ajena)]},
        )
        .scalars()
        .all()
    )
    assert remaining == [smoke._thread(ajena)]  # la conversación previa no se toca


@requires_postgres
def test_cleanup_corre_igual_si_no_se_creo_nada(db_session: Session, make_profile) -> None:
    make_profile()
    run = _run_with(db_session)

    detail = smoke._cleanup(run)

    assert "movimientos_borrados=0" in detail
    assert "drafts_borrados=0" in detail
    assert "checkpoints_borrados=0" in detail


def test_instrumentacion_cuenta_los_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        embeddings_module.OpenAIEmbeddingProvider, "embed", lambda self, text: [0.1, 0.2]
    )
    budget = smoke.CallBudget(1)
    smoke._install_budget(budget)

    provider = embeddings_module.OpenAIEmbeddingProvider(smoke.settings)
    assert provider.embed("texto") == [0.1, 0.2]
    assert budget.used == 1
    assert budget.records[0].kind == "embeddings.create"

    with pytest.raises(smoke.BudgetExceededError):
        provider.embed("otro texto")
