"""Indexación de movimientos para el RAG. Mantiene `transaction_search_documents` al día.

- Al crear/editar un movimiento: upsert de su documento (texto + tsvector + embedding).
- Al eliminarlo: se borra por cascade (FK ON DELETE CASCADE), o con `remove_transaction`.
- `backfill` reconstruye el índice para datos existentes.

`content_hash` evita recomputar el embedding si el texto no cambió. Todo corre dentro de un
SAVEPOINT: si la indexación falla, el movimiento igual se guarda (nunca rompe una escritura).
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai.rag.embeddings import EMBEDDING_VERSION, build_embedding_provider
from app.core.config import settings
from app.models.transaction import Transaction
from app.models.transaction_search import TransactionSearchDocument

logger = logging.getLogger("app.ai.rag.indexer")

_TYPE_WORDS = {"income": "ingreso cobro entrada", "expense": "gasto pago salida"}


def build_searchable_text(tx: Transaction) -> str:
    parts = [
        _TYPE_WORDS.get(tx.type.value, ""),
        tx.category or "",
        tx.description or "",
        tx.payment_method or "",
        tx.occurred_on.isoformat(),
    ]
    return " ".join(p for p in parts if p).strip()


def _content_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}:{EMBEDDING_VERSION}:{text}".encode()).hexdigest()


def index_transaction(session: Session, tx: Transaction) -> None:
    """Upsert best-effort del documento de búsqueda de un movimiento."""
    try:
        with session.begin_nested():
            _upsert(session, tx)
    except Exception:  # noqa: BLE001 - indexar nunca debe romper la escritura
        logger.warning("No se pudo indexar el movimiento %s", tx.id, exc_info=True)


def _upsert(session: Session, tx: Transaction) -> None:
    provider = build_embedding_provider(settings)
    text = build_searchable_text(tx)
    content_hash = _content_hash(text, provider.model)

    doc = session.execute(
        select(TransactionSearchDocument).where(TransactionSearchDocument.transaction_id == tx.id)
    ).scalar_one_or_none()

    if doc is not None and doc.content_hash == content_hash:
        _refresh_denormalized(doc, tx, text)
        session.flush()
        _set_tsvector(session, doc.id, text)
        return

    embedding = provider.embed(text)
    if doc is None:
        doc = TransactionSearchDocument(
            user_id=tx.user_id,
            transaction_id=tx.id,
            searchable_text=text,
            embedding=embedding,
            embedding_model=provider.model,
            embedding_version=EMBEDDING_VERSION,
            content_hash=content_hash,
            occurred_on=tx.occurred_on,
            amount=tx.amount,
            category=tx.category,
        )
        session.add(doc)
    else:
        doc.searchable_text = text
        doc.embedding = embedding
        doc.embedding_model = provider.model
        doc.embedding_version = EMBEDDING_VERSION
        doc.content_hash = content_hash
        _refresh_denormalized(doc, tx, text)
    session.flush()
    _set_tsvector(session, doc.id, text)


def _refresh_denormalized(doc: TransactionSearchDocument, tx: Transaction, text: str) -> None:
    doc.occurred_on = tx.occurred_on
    doc.amount = tx.amount
    doc.category = tx.category
    doc.searchable_text = text


def _set_tsvector(session: Session, doc_id, text: str) -> None:
    session.execute(
        update(TransactionSearchDocument)
        .where(TransactionSearchDocument.id == doc_id)
        .values(text_search_vector=func.to_tsvector("spanish", text))
    )


def remove_transaction(session: Session, transaction_id) -> None:
    session.execute(
        TransactionSearchDocument.__table__.delete().where(
            TransactionSearchDocument.transaction_id == transaction_id
        )
    )


def backfill(session: Session) -> int:
    """Reindexa todos los movimientos existentes. Devuelve cuántos indexó."""
    count = 0
    for tx in session.execute(select(Transaction)).scalars():
        _upsert(session, tx)
        count += 1
    session.commit()
    return count
