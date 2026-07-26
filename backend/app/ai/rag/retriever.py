"""Recuperación híbrida: full-text (PostgreSQL) + semántica (pgvector), fusionadas con RRF.

`user_id` es autoritativo y se filtra SIEMPRE: nunca se recupera un movimiento de otro
usuario. El LLM no participa acá; esto solo *encuentra* candidatos. Las sumas exactas las
hace `structured_expense_total` con SQL determinístico.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.ai.rag.embeddings import build_embedding_provider
from app.core.config import settings
from app.models.transaction import Transaction
from app.models.transaction_search import TransactionSearchDocument

RRF_K = 60


@dataclass
class SearchFilters:
    category: str | None = None
    tx_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass
class Candidate:
    transaction_id: UUID
    searchable_text: str
    occurred_on: date
    amount: Decimal
    category: str
    tx_type: str
    methods: set[str] = field(default_factory=set)
    rrf_score: float = 0.0
    vector_distance: Decimal | None = None
    relevance_reasons: list[str] = field(default_factory=list)


def _apply_filters(stmt: Select, user_id: UUID, filters: SearchFilters) -> Select:
    stmt = stmt.where(TransactionSearchDocument.user_id == user_id)
    if filters.category:
        stmt = stmt.where(TransactionSearchDocument.category == filters.category.lower())
    if filters.tx_type:
        stmt = stmt.where(Transaction.type == filters.tx_type)
    if filters.date_from:
        stmt = stmt.where(TransactionSearchDocument.occurred_on >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(TransactionSearchDocument.occurred_on <= filters.date_to)
    return stmt


class HybridRetriever:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int = 5,
        filters: SearchFilters | None = None,
    ) -> list[Candidate]:
        filters = filters or SearchFilters()
        pool = max(top_k * 3, 10)
        text_ids = self._full_text(user_id, query, filters, pool)
        vector_ids = self._vector(user_id, query, filters, pool)

        candidates: dict[UUID, Candidate] = {}
        for rank, row in enumerate(text_ids):
            self._fuse(candidates, row, rank, "full_text")
        for rank, row in enumerate(vector_ids):
            self._fuse(candidates, row, rank, "vector")

        ranked = sorted(candidates.values(), key=lambda c: c.rrf_score, reverse=True)
        return ranked[:top_k]

    def _fuse(self, acc: dict[UUID, Candidate], row: Any, rank: int, method: str) -> None:
        cand = acc.get(row.transaction_id)
        if cand is None:
            cand = Candidate(
                transaction_id=row.transaction_id,
                searchable_text=row.searchable_text,
                occurred_on=row.occurred_on,
                amount=row.amount,
                category=row.category,
                tx_type=row.tx_type,
            )
            acc[row.transaction_id] = cand
        cand.methods.add(method)
        cand.rrf_score += 1.0 / (RRF_K + rank)
        distance = getattr(row, "vector_distance", None)
        if distance is not None:
            cand.vector_distance = Decimal(str(distance))

    def _base_columns(self) -> Select:
        return select(
            TransactionSearchDocument.transaction_id,
            TransactionSearchDocument.searchable_text,
            TransactionSearchDocument.occurred_on,
            TransactionSearchDocument.amount,
            TransactionSearchDocument.category,
            Transaction.type.label("tx_type"),
        ).join(
            Transaction,
            Transaction.id == TransactionSearchDocument.transaction_id,
        )

    def _full_text(self, user_id: UUID, query: str, filters: SearchFilters, pool: int) -> list[Any]:
        tsquery = func.plainto_tsquery("spanish", query)
        rank = func.ts_rank(TransactionSearchDocument.text_search_vector, tsquery)
        stmt = _apply_filters(self._base_columns(), user_id, filters)
        stmt = stmt.where(TransactionSearchDocument.text_search_vector.op("@@")(tsquery))
        stmt = stmt.order_by(rank.desc()).limit(pool)
        return list(self._session.execute(stmt).all())

    def _vector(self, user_id: UUID, query: str, filters: SearchFilters, pool: int) -> list[Any]:
        provider = build_embedding_provider(settings)
        qvec = provider.embed(query)
        distance = TransactionSearchDocument.embedding.cosine_distance(qvec)
        stmt = _apply_filters(
            self._base_columns().add_columns(distance.label("vector_distance")), user_id, filters
        )
        stmt = stmt.where(TransactionSearchDocument.embedding.is_not(None))
        stmt = stmt.order_by(distance.asc()).limit(pool)
        return list(self._session.execute(stmt).all())


def structured_total(session: Session, user_id: UUID, filters: SearchFilters) -> dict[str, Any]:
    """Suma exacta (SQL) de movimientos que cumplen los filtros. Nada de LLM."""
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0), func.count(Transaction.id)).where(
        Transaction.user_id == user_id
    )
    if filters.tx_type:
        stmt = stmt.where(Transaction.type == filters.tx_type)
    if filters.category:
        stmt = stmt.where(Transaction.category == filters.category.lower())
    if filters.date_from:
        stmt = stmt.where(Transaction.occurred_on >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Transaction.occurred_on <= filters.date_to)
    total, count = session.execute(stmt).one()
    return {"total": Decimal(total), "count": int(count)}


def select_relevant(
    candidates: list[Candidate],
    *,
    vector_max_distance: float,
    limit: int,
) -> list[Candidate]:
    selected: list[Candidate] = []
    threshold = Decimal(str(vector_max_distance))
    for candidate in candidates:
        reasons: list[str] = []
        if "full_text" in candidate.methods:
            reasons.append("full_text_match")
        if candidate.vector_distance is not None and candidate.vector_distance <= threshold:
            reasons.append("vector_similarity")
        if len(candidate.methods) > 1 and reasons:
            reasons.append("rrf_hybrid")
        if not reasons:
            continue
        candidate.relevance_reasons = reasons
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def selected_total(
    session: Session,
    user_id: UUID,
    transaction_ids: list[UUID],
    *,
    tx_type: str | None = None,
) -> dict[str, Any]:
    """Suma exacta (SQL) limitada a IDs recuperados y al usuario autoritativo."""
    if not transaction_ids:
        return {"total": Decimal("0"), "count": 0}
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0), func.count(Transaction.id)).where(
        Transaction.user_id == user_id,
        Transaction.id.in_(transaction_ids),
    )
    if tx_type:
        stmt = stmt.where(Transaction.type == tx_type)
    total, count = session.execute(stmt).one()
    return {"total": Decimal(total), "count": int(count)}


def structured_expense_total(
    session: Session, user_id: UUID, filters: SearchFilters
) -> dict[str, Any]:
    """Compatibilidad: suma exacta de gastos que cumplen filtros."""
    return structured_total(
        session,
        user_id,
        SearchFilters(
            category=filters.category,
            tx_type="expense",
            date_from=filters.date_from,
            date_to=filters.date_to,
        ),
    )
