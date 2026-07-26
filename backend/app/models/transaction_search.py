from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base

_EMBEDDING_DIM = settings.ai_embedding_dimension


class TransactionSearchDocument(Base):
    """Documento de búsqueda de un movimiento para el RAG híbrido.

    Separa el índice (texto + embedding) del dato financiero: los embeddings nunca entran
    en un cálculo de saldo. `content_hash` evita reindexar si el texto no cambió.
    """

    __tablename__ = "transaction_search_documents"
    __table_args__ = (
        Index(
            "ix_transaction_search_documents_tsv",
            "text_search_vector",
            postgresql_using="gin",
        ),
        Index("ix_transaction_search_documents_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    transaction_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Copias desnormalizadas para filtrar/mostrar evidencia sin volver a la tabla origen.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
