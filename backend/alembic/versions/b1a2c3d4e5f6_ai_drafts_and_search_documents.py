"""ai_drafts, pgvector y transaction_search_documents

Revision ID: b1a2c3d4e5f6
Revises: 7696d40fb558
Create Date: 2026-07-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1a2c3d4e5f6"
down_revision: str | Sequence[str] | None = "7696d40fb558"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "ai_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task", sa.String(length=60), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_drafts")),
    )
    op.create_index(
        "ix_ai_drafts_status_expires_at", "ai_drafts", ["status", "expires_at"], unique=False
    )

    op.create_table(
        "transaction_search_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("searchable_text", sa.Text(), nullable=False),
        sa.Column("text_search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("embedding_version", sa.String(length=20), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_transaction_search_documents_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction_search_documents")),
        sa.UniqueConstraint(
            "transaction_id", name=op.f("uq_transaction_search_documents_transaction_id")
        ),
    )
    op.create_index(
        "ix_transaction_search_documents_user_id",
        "transaction_search_documents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_search_documents_tsv",
        "transaction_search_documents",
        ["text_search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_search_documents_tsv", table_name="transaction_search_documents")
    op.drop_index(
        "ix_transaction_search_documents_user_id", table_name="transaction_search_documents"
    )
    op.drop_table("transaction_search_documents")
    op.drop_index("ix_ai_drafts_status_expires_at", table_name="ai_drafts")
    op.drop_table("ai_drafts")
