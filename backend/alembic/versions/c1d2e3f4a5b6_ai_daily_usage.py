"""ai_daily_usage: contadores diarios de uso de IA por usuario

Revision ID: c1d2e3f4a5b6
Revises: b1a2c3d4e5f6
Create Date: 2026-07-29 17:00:00.000000

Tabla chica y sin foreign keys: el contador tiene que existir aunque la persona todavía
no haya creado su perfil financiero. La clave primaria compuesta (usuario, día, tipo) es
la que habilita el incremento atómico con `INSERT ... ON CONFLICT DO UPDATE`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b1a2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_daily_usage",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("usage_day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("used", sa.Integer(), server_default="0", nullable=False),
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
        sa.PrimaryKeyConstraint("user_id", "usage_day", "kind"),
    )


def downgrade() -> None:
    op.drop_table("ai_daily_usage")
