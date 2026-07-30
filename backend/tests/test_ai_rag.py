"""Tests del RAG híbrido: indexación, full-text, vector, RRF, filtros y aislamiento."""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.agent.tools import ToolContext, run_tool
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.ai.rag.embeddings import MockEmbeddingProvider
from app.ai.rag.indexer import backfill
from app.ai.rag.retriever import HybridRetriever, SearchFilters, structured_expense_total
from app.core.constants import DEMO_USER_ID
from app.models.transaction_search import TransactionSearchDocument
from app.schemas.transaction import TransactionCreate
from app.services import transaction_service as ts
from app.services.draft_store import InMemoryDraftStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres


def _tx(
    session: Session,
    category: str,
    amount: str,
    desc: str,
    day: int = 20,
    tx_type: str = "expense",
):
    return ts.create_transaction(
        session,
        DEMO_USER_ID,
        TransactionCreate(
            type=tx_type,
            amount=Decimal(amount),
            category=category,
            description=desc,
            occurred_on=date(2026, 7, day),
        ),
    )


def test_indexa_al_crear(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    tx = _tx(db_session, "transporte", "12000", "carga de nafta")
    doc = db_session.execute(
        select(TransactionSearchDocument).where(TransactionSearchDocument.transaction_id == tx.id)
    ).scalar_one()
    assert doc.embedding is not None
    assert "nafta" in doc.searchable_text


def test_full_text_encuentra_por_palabra(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _tx(db_session, "transporte", "12000", "carga de nafta")
    _tx(db_session, "gastronomía", "8000", "cafe con leche")
    res = HybridRetriever(db_session).search(user_id=DEMO_USER_ID, query="nafta", top_k=5)
    assert any("nafta" in c.searchable_text for c in res)


def test_hibrida_marca_metodos(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    _tx(db_session, "transporte", "12000", "carga de nafta")
    res = HybridRetriever(db_session).search(user_id=DEMO_USER_ID, query="nafta", top_k=5)
    assert res
    assert res[0].methods == {"full_text", "vector"}
    assert res[0].rrf_score > 0


def test_structured_total_exacto(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    _tx(db_session, "transporte", "12000", "nafta")
    _tx(db_session, "transporte", "15000", "nafta ruta")
    _tx(db_session, "gastronomía", "8000", "cafe")
    agg = structured_expense_total(db_session, DEMO_USER_ID, SearchFilters(category="transporte"))
    assert agg["total"] == Decimal("27000.00")
    assert agg["count"] == 2


def test_filtro_por_categoria(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    _tx(db_session, "transporte", "12000", "nafta")
    _tx(db_session, "supermercado", "42000", "compra")
    res = HybridRetriever(db_session).search(
        user_id=DEMO_USER_ID,
        query="compra",
        top_k=5,
        filters=SearchFilters(category="supermercado"),
    )
    assert all(c.category == "supermercado" for c in res)


def test_filtro_tx_type_no_mezcla_gastos_e_ingresos(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    expense = _tx(db_session, "transporte", "12000", "nafta reintegro")
    income = _tx(db_session, "transporte", "50000", "nafta reintegro", tx_type="income")

    gastos = HybridRetriever(db_session).search(
        user_id=DEMO_USER_ID,
        query="nafta reintegro",
        top_k=5,
        filters=SearchFilters(tx_type="expense"),
    )
    ingresos = HybridRetriever(db_session).search(
        user_id=DEMO_USER_ID,
        query="nafta reintegro",
        top_k=5,
        filters=SearchFilters(tx_type="income"),
    )

    assert {c.transaction_id for c in gastos} == {expense.id}
    assert {c.transaction_id for c in ingresos} == {income.id}


def test_tool_search_con_fecha_suma_solo_ids_relevantes(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _tx(db_session, "transporte", "12000", "carga de nafta", day=10)
    _tx(db_session, "gastronomía", "8000", "café", day=10)
    _tx(db_session, "supermercado", "42000", "compra mensual", day=10)
    _tx(db_session, "transporte", "50000", "nafta reintegro", day=10, tx_type="income")
    ctx = ToolContext(
        session=db_session,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=date(2026, 7, 24),
        user_id=DEMO_USER_ID,
    )

    result = run_tool(
        ctx,
        "search_transactions",
        {
            "query": "nafta",
            "tx_type": "expense",
            "date_from": "2026-07-01",
            "date_to": "2026-07-31",
        },
    )

    assert result["ok"] is True
    assert result["data"]["total"] == "12000.00"
    assert result["data"]["count"] == 1
    assert len(result["data"]["evidence"]) == 1


def test_tool_search_sin_evidencia_no_inventa_total(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _tx(db_session, "gastronomía", "8000", "café", day=10)
    ctx = ToolContext(
        session=db_session,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=date(2026, 7, 24),
        user_id=DEMO_USER_ID,
    )

    result = run_tool(
        ctx,
        "search_transactions",
        {"query": "neumático espacial", "tx_type": "expense", "top_k": 5},
    )

    assert result["ok"] is True
    assert result["data"]["count"] == 0
    assert result["data"]["total"] == "0"
    assert result["data"]["not_enough_evidence"] is True


def test_aislamiento_por_usuario(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    tx = _tx(db_session, "transporte", "12000", "nafta")
    doc = db_session.execute(
        select(TransactionSearchDocument).where(TransactionSearchDocument.transaction_id == tx.id)
    ).scalar_one()
    doc.user_id = uuid.uuid4()  # el doc pasa a ser de otro usuario
    db_session.flush()
    res = HybridRetriever(db_session).search(user_id=DEMO_USER_ID, query="nafta", top_k=5)
    assert all(c.transaction_id != tx.id for c in res)


def test_borrar_movimiento_borra_el_indice(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    tx = _tx(db_session, "transporte", "12000", "nafta")
    ts.delete_transaction(db_session, DEMO_USER_ID, tx.id)
    doc = db_session.execute(
        select(TransactionSearchDocument).where(TransactionSearchDocument.transaction_id == tx.id)
    ).scalar_one_or_none()
    assert doc is None


def test_content_hash_evita_reindexar(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    tx = _tx(db_session, "transporte", "12000", "nafta")
    doc = db_session.execute(
        select(TransactionSearchDocument).where(TransactionSearchDocument.transaction_id == tx.id)
    ).scalar_one()
    first_hash = doc.content_hash
    count = backfill(db_session)  # reindexa todo
    db_session.refresh(doc)
    assert count >= 1
    assert doc.content_hash == first_hash


def test_mock_embeddings_deterministicos() -> None:
    provider = MockEmbeddingProvider(64)
    assert provider.embed("nafta ruta") == provider.embed("nafta ruta")
    assert provider.embed("nafta") != provider.embed("cafe")
