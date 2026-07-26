from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.ai.agent.graph import close_checkpointer
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Transaction
from app.services import ai_chat_service, transaction_service


def _require_real_config() -> None:
    if os.getenv("RUN_REAL_AI_TESTS") != "1":
        raise SystemExit("Set RUN_REAL_AI_TESTS=1 to run the real AI smoke test.")
    if settings.ai_provider != "openai":
        raise SystemExit("Set AI_PROVIDER=openai.")
    if not settings.ai_api_key:
        raise SystemExit("Set AI_API_KEY locally. It will not be printed.")
    if settings.ai_model.startswith("mock-"):
        raise SystemExit("Set AI_MODEL to a real Responses-compatible model.")


def main() -> None:
    _require_real_config()
    created_amount = Decimal("1234.00")
    with SessionLocal() as session:
        summary = ai_chat_service.chat(session, "¿Cuánto puedo gastar hoy?", as_of=date.today())
        print("summary_intent=", summary.intent.value, "approval=", summary.requires_approval)

        search = ai_chat_service.chat(
            session,
            "Buscá gastos recientes y después resumime la evidencia",
            conversation_id=summary.conversation_id,
            as_of=date.today(),
        )
        print("search_intent=", search.intent.value, "evidence=", len(search.evidence))

        draft = ai_chat_service.chat(
            session,
            "Gasté 1234 pesos hoy en prueba smoke con débito",
            conversation_id=summary.conversation_id,
            as_of=date.today(),
        )
        print("draft_requires_approval=", draft.requires_approval)
        if not draft.pending_action:
            raise SystemExit("The real model did not prepare a pending action.")

        approved = ai_chat_service.resume(
            session,
            draft.conversation_id,
            draft.pending_action.action_id,
            approve=True,
            as_of=date.today(),
        )
        print("approved=", not approved.requires_approval)

        txs = session.execute(
            select(Transaction).where(
                Transaction.amount == created_amount,
                Transaction.description.ilike("%smoke%"),
            )
        ).scalars()
        for tx in list(txs):
            transaction_service.delete_transaction(session, tx.id)
        print("cleanup_done=True")
    close_checkpointer()


if __name__ == "__main__":
    main()
