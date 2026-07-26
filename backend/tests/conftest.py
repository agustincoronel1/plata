"""Infraestructura de los tests de integración con PostgreSQL.

Los tests de integración (los que piden la fixture `client` o `db_session`) corren
contra el PostgreSQL de desarrollo, pero de forma transaccional: cada test abre una
transacción externa, deja que los endpoints hagan `commit` con normalidad —esos commits
son savepoints internos gracias a `join_transaction_mode="create_savepoint"`— y al
terminar hace rollback de la transacción externa. Nada se persiste fuera del test, así
que los datos demo nunca se alteran de forma permanente.

Si PostgreSQL no está disponible, estos tests se SALTAN con un mensaje claro (no se
marcan como pasados). Los tests que no dependen de la base (health, metadatos, seed,
schemas) no usan estas fixtures y corren siempre.
"""

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import DEMO_USER_ID
from app.core.database import engine, get_db
from app.main import app
from app.models import UserProfile

API = "/api/v1"
settings.ai_checkpoint_store = "memory"


def _postgres_available() -> bool:
    try:
        connection = engine.connect()
        connection.close()
    except SQLAlchemyError:
        return False
    return True


POSTGRES_UP = _postgres_available()

# Marca de salto reutilizable: informa que el test es de integración y por qué se omite.
requires_postgres = pytest.mark.skipif(
    not POSTGRES_UP,
    reason=(
        "PostgreSQL no disponible: test de integración omitido. "
        "Levantá la base con 'docker compose up -d db' y aplicá 'alembic upgrade head'."
    ),
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Sesión transaccional aislada. Parte de una base sin el perfil demo.

    Borra el perfil demo dentro de la transacción externa (el ON DELETE CASCADE se lleva
    sus movimientos y compromisos) para que cada test arranque de un estado conocido e
    independiente del seed. Como todo cuelga de la transacción externa, el rollback final
    restaura la base intacta.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    existing = session.get(UserProfile, DEMO_USER_ID)
    if existing is not None:
        session.delete(existing)
        session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient con get_db apuntando a la sesión transaccional del test."""

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def default_profile_payload(**overrides: object) -> dict[str, object]:
    """Payload de un perfil demo con saldo conocido (620000), sobreescribible por test."""
    payload: dict[str, object] = {
        "name": "Agustín Demo",
        "currency": "ARS",
        "current_balance": "620000.00",
        "next_income_amount": "1200000.00",
        "next_income_date": "2026-08-01",
        "protected_amount": "120000.00",
        "safety_buffer": "40000.00",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def make_profile(client: TestClient) -> Callable[..., dict]:
    """Crea el perfil demo vía la API y devuelve su representación JSON."""

    def _make(**overrides: object) -> dict:
        response = client.put(f"{API}/profile", json=default_profile_payload(**overrides))
        assert response.status_code == 200, response.text
        return response.json()

    return _make
