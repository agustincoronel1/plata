"""normaliza las categorías de gastos al vocabulario fijo

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-04 10:00:00.000000

Migración SOLO de datos: no crea, borra ni altera ninguna columna. La columna `category`
ya existía (String(60), NOT NULL); lo que cambia es que, para los GASTOS, los valores
pasan a pertenecer a la lista fija de `app.services.categorizer`.

Cada gasto se reclasifica con las mismas reglas determinísticas que usa la aplicación:
la categoría vieja entra como pista junto con la descripción ("supermercado" -> "comida"),
y lo que no coincide con ninguna regla queda en "otros". Los INGRESOS no se tocan: siguen
con su categoría de texto libre.

Es idempotente (volver a correrla no cambia nada) y reversible en el sentido práctico: el
downgrade no restaura los textos originales porque no se guardan en ningún lado, así que
no hace nada. Antes de aplicarla en producción conviene tener el backup habitual de la
base; ningún dato monetario se modifica.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.categorizer import resolve_expense_category

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SELECT_EXPENSES = sa.text(
    "SELECT id, category, description FROM transactions WHERE type = 'expense'"
)
_UPDATE_CATEGORY = sa.text("UPDATE transactions SET category = :category WHERE id = :id")


def upgrade() -> None:
    connection = op.get_bind()
    for row in connection.execute(_SELECT_EXPENSES).mappings().all():
        resolved = resolve_expense_category(row["category"], row["description"])
        if resolved != row["category"]:
            connection.execute(_UPDATE_CATEGORY, {"category": resolved, "id": row["id"]})


def downgrade() -> None:
    """No revierte nada: las categorías originales de texto libre no se conservan."""
