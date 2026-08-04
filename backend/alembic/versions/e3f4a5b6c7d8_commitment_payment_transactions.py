"""vincula compromisos pagados con movimientos reales

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("commitments", "category", server_default=sa.text("'otros'"))
    op.add_column("transactions", sa.Column("commitment_id", sa.Uuid(), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'ARS'"), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_transactions_commitment_id_commitments"),
        "transactions",
        "commitments",
        ["commitment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_transactions_commitment_id"),
        "transactions",
        ["commitment_id"],
    )
    op.create_check_constraint(
        op.f("ck_transactions_currency_uppercase"),
        "transactions",
        "currency = upper(currency)",
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE transactions DROP CONSTRAINT IF EXISTS ck_transactions_currency_uppercase"
    )
    op.drop_constraint(op.f("uq_transactions_commitment_id"), "transactions", type_="unique")
    op.drop_constraint(
        op.f("fk_transactions_commitment_id_commitments"),
        "transactions",
        type_="foreignkey",
    )
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS commitment_id")
    op.alter_column("commitments", "category", server_default=None)
