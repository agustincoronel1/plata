"""Tests de las tools del copiloto: schemas, aislamiento y que las escrituras no persisten."""

from collections.abc import Callable
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.agent.tools import ToolContext, run_tool
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.models import PurchaseSimulation
from app.services.draft_store import InMemoryDraftStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres

AS_OF = date(2026, 7, 24)


def _ctx(session: Session) -> ToolContext:
    return ToolContext(
        session=session,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=AS_OF,
    )


def test_get_financial_summary_ok(db_session: Session, make_profile: Callable[..., dict]) -> None:
    make_profile()
    rec = run_tool(_ctx(db_session), "get_financial_summary", {})
    assert rec["ok"] is True
    assert "spendable_total" in rec["data"]


def test_argumentos_invalidos_devuelven_error_seguro(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    rec = run_tool(_ctx(db_session), "simulate_purchase_preview", {"total_amount": "-5"})
    assert rec["ok"] is False
    assert rec["data"] is None


def test_simulate_preview_no_persiste(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    before = db_session.execute(select(func.count(PurchaseSimulation.id))).scalar_one()
    rec = run_tool(
        _ctx(db_session),
        "simulate_purchase_preview",
        {"total_amount": "900000", "installments": 9},
    )
    after = db_session.execute(select(func.count(PurchaseSimulation.id))).scalar_one()
    assert rec["ok"] is True
    assert before == after  # una vista previa no guarda nada


def test_create_transaction_draft_prepara_borrador(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    ctx = _ctx(db_session)
    rec = run_tool(
        ctx, "create_transaction_draft", {"text": "Gasté 25 lucas ayer en nafta con débito"}
    )
    assert rec["ok"] is True
    assert rec["writes"] is True
    assert rec["data"]["is_confirmable"] is True
    assert rec["data"]["draft_id"]
