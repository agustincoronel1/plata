"""Entorno de Alembic.

La URL de la base sale de `Settings`, nunca de alembic.ini: así no hay credenciales en
archivos versionados y hay una sola fuente de configuración.
"""

from logging.config import fileConfig

# `app.models` se importa por su efecto colateral: registra las cuatro tablas en
# Base.metadata. Sin ese import el autogenerate no ve ningún modelo.
import app.models  # noqa: F401
from alembic import context
from app.core.config import settings
from app.core.database import Base, engine, include_object

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse a la base."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica las migraciones sobre una conexión real.

    Reutiliza el engine de la aplicación para no duplicar la configuración.
    """
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
