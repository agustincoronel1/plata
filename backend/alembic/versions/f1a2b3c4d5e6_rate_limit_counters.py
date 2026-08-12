"""rate_limit_counters: contadores de rate limiting compartidos entre instancias

Revision ID: f1a2b3c4d5e6
Revises: e3f4a5b6c7d8
Create Date: 2026-08-12 10:00:00.000000

Tabla chica y sin foreign keys: el límite tiene que poder aplicarse a peticiones sin sesión
(por IP) y a cuentas que todavía no crearon su perfil. La clave primaria compuesta
(scope, sujeto, ventana) es la que habilita el incremento atómico con
`INSERT ... ON CONFLICT DO UPDATE`, igual que en `ai_daily_usage`.

`subject_hash` guarda un HMAC-SHA256, nunca una IP en claro (ver `app.core.rate_limit`).

El índice sobre `expires_at` es para la limpieza: sin él, borrar ventanas vencidas obliga a
recorrer la tabla entera.

No es destructiva: solo crea una tabla nueva.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("scope", sa.String(length=60), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("scope", "subject_hash", "window_start"),
    )
    op.create_index(
        "ix_rate_limit_counters_expires_at",
        "rate_limit_counters",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_counters_expires_at", table_name="rate_limit_counters")
    op.drop_table("rate_limit_counters")
