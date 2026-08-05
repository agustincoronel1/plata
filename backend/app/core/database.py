from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# Sin esta convención, PostgreSQL inventa los nombres de constraints e índices y las
# migraciones quedan atadas a nombres que Alembic no puede reproducir ni revertir.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# create_engine no abre conexiones: el pool las crea bajo demanda. La API arranca
# aunque PostgreSQL esté detenido.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Sin esto el healthcheck queda colgado decenas de segundos con la base caída.
    connect_args={"connect_timeout": 3},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Base declarativa de los modelos."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Tablas que crea y versiona LangGraph, no Alembic. Sin esta exclusión, comparar los
# modelos contra la base propone borrarlas. Vive acá, junto a la metadata, para que
# `alembic/env.py` y los tests que comparan esquema usen exactamente el mismo criterio.
EXTERNAL_TABLES = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Filtro de autogenerate: ignora las tablas que Alembic no administra."""
    if type_ == "table" and name in EXTERNAL_TABLES:
        return False
    return True


def get_db() -> Generator[Session, None, None]:
    """Dependencia de FastAPI: entrega una sesión y la cierra siempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
