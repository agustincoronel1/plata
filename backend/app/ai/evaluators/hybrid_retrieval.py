"""Evaluación del RAG híbrido con datos sembrados (requiere PostgreSQL, mock embeddings).

python -m app.ai.evaluators.hybrid_retrieval
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal

from app.ai.evaluators._common import EVAL_USER_ID, Metric, load_jsonl, print_report
from app.ai.evaluators._dbharness import postgres_available, seeded_session
from app.ai.rag.retriever import (
    HybridRetriever,
    SearchFilters,
    select_relevant,
    selected_total,
    structured_expense_total,
)
from app.core.config import settings

DATASET = "hybrid_retrieval.jsonl"


def run() -> int:
    if not postgres_available():
        print("hybrid_retrieval: PostgreSQL no disponible, omito (exit 0).")
        return 0

    precision = Metric("precision_at_k")
    recall = Metric("recall_at_k")
    mrr = Metric("mrr")
    isolation = Metric("user_isolation_rate")
    structured = Metric("structured_query_accuracy")
    rejection = Metric("irrelevant_candidate_rejection_rate")
    aggregate = Metric("aggregate_correctness")
    failures: list[str] = []

    with seeded_session() as session:
        retriever = HybridRetriever(session)
        for row in load_jsonl(DATASET):
            k = row["top_k"]
            cands = retriever.search(user_id=EVAL_USER_ID, query=row["query"], top_k=k)
            selected = select_relevant(
                cands,
                vector_max_distance=settings.ai_rag_vector_max_distance,
                limit=settings.ai_rag_max_evidence,
            )
            relevant = [c for c in cands if c.category == row["relevant_category"]]
            precision.add(bool(cands) and len(relevant) / len(cands) > 0)
            recall.add(len(relevant) >= 1)
            rank = next(
                (i + 1 for i, c in enumerate(cands) if c.category == row["relevant_category"]), None
            )
            mrr.add(rank is not None)
            if rank is None:
                failures.append(f"'{row['query']}' no recuperó {row['relevant_category']}")
            rejection.add(all(c.category == row["relevant_category"] for c in selected))
            agg = selected_total(
                session,
                EVAL_USER_ID,
                [c.transaction_id for c in selected],
                tx_type="expense",
            )
            aggregate.add(agg["count"] == len([c for c in selected if c.tx_type == "expense"]))

        # Aislamiento: una búsqueda con otro user_id no devuelve nada del usuario demo.
        other = retriever.search(user_id=uuid.uuid4(), query="nafta", top_k=5)
        isolation.add(len(other) == 0)

        # Consulta estructurada exacta (SQL, no LLM).
        agg = structured_expense_total(session, EVAL_USER_ID, SearchFilters(category="transporte"))
        structured.add(agg["total"] == Decimal("27000.00") and agg["count"] == 2)

    metrics = [precision, recall, mrr, rejection, aggregate, isolation, structured]
    print_report("hybrid_retrieval (mock embeddings)", metrics, failures)
    return 0 if recall.rate == 1.0 and isolation.rate == 1.0 and structured.rate == 1.0 else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
