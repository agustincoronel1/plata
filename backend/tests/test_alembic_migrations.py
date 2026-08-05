"""Guardas sobre las migraciones. Existen por un incidente real.

Un despliegue subió código que consultaba `transactions.commitment_id` contra una base
donde la migración que crea esa columna nunca se había aplicado: todo `PATCH` de un
compromiso a pagado respondía 500 con `UndefinedColumn`. Ninguna suite lo detectaba
porque la base de desarrollo sí estaba migrada.

De acá salen las tres preguntas que hay que poder responder antes de desplegar:
¿hay una sola head?, ¿la base está en esa head?, ¿el esquema de la base coincide con lo
que piden los modelos?
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.database import Base, engine, include_object
from tests.conftest import requires_postgres

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def test_hay_una_sola_head(script_directory: ScriptDirectory) -> None:
    """Dos heads dejan `alembic upgrade head` sin destino único y rompen el despliegue."""
    assert len(script_directory.get_heads()) == 1


def test_la_cadena_de_revisiones_no_tiene_huecos(script_directory: ScriptDirectory) -> None:
    """Cada revisión enlaza con la anterior hasta la base, sin ramas sueltas."""
    revisions = list(script_directory.walk_revisions())
    assert revisions[-1].down_revision is None
    for revision in revisions[:-1]:
        assert revision.down_revision is not None
        assert script_directory.get_revision(revision.down_revision) is not None


@requires_postgres
def test_la_base_esta_en_la_ultima_revision(script_directory: ScriptDirectory) -> None:
    """La base tiene aplicada la head del repo. Esta es la falla que causó el 500."""
    with engine.connect() as connection:
        applied = MigrationContext.configure(connection).get_current_revision()

    head = script_directory.get_current_head()
    assert applied == head, (
        f"La base está en {applied} y el repo en {head}. Ejecutá 'python -m alembic upgrade head'."
    )


@requires_postgres
def test_el_esquema_de_la_base_coincide_con_los_modelos() -> None:
    """Sin diferencias entre lo que declaran los modelos y lo que existe en PostgreSQL.

    Es el equivalente de `alembic check`: una columna que el modelo declara y ninguna
    migración crea se detecta acá y no con un 500 en producción.
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "include_object": include_object},
        )
        differences = compare_metadata(context, Base.metadata)

    assert differences == []
