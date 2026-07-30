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
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import DEMO_USER_ID
from app.core.database import engine, get_db
from app.core.security import get_current_user
from app.main import app
from app.models import UserProfile
from app.schemas.auth import AuthenticatedUser

API = "/api/v1"

# Usuario autenticado por defecto en los tests. Es el mismo UUID del perfil demo: así los
# tests que ya existían siguen describiendo el mismo escenario, y la fixture `db_session`
# sigue partiendo de una base limpia. Verificar el JWT de verdad ya tiene sus propios
# tests (test_auth_jwt.py); acá lo que se prueba es qué hace la API con la identidad ya
# resuelta, así que la dependencia se sustituye en lugar de firmar tokens en cada test.
TEST_USER_ID = DEMO_USER_ID
TEST_USER_EMAIL = "demo@plata.test"

# Segundo usuario, para los tests de aislamiento entre cuentas.
OTHER_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_USER_EMAIL = "otra-persona@plata.test"
settings.ai_checkpoint_store = "memory"

# Los tests NUNCA hablan con un proveedor real, pase lo que pase en backend/.env. Con
# AI_PROVIDER=openai y una API key configurada, correr `pytest` gastaría plata de
# verdad y dejaría de ser determinístico. Forzar los proveedores mock acá deja la suite
# offline, gratis y reproducible; la validación real vive solo en app/scripts/real_ai_smoke.py.
settings.ai_provider = "mock"
settings.ai_model = "mock-transaction-parser-v1"
settings.ai_api_key = ""
settings.ai_embedding_provider = "mock"


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


def _authenticated_as(user_id: UUID, email: str | None) -> Callable[[], AuthenticatedUser]:
    """Sustituto de `get_current_user`: devuelve una identidad ya verificada."""

    def override() -> AuthenticatedUser:
        return AuthenticatedUser(id=user_id, email=email)

    return override


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient autenticado como el usuario de test, sobre la sesión del test.

    Sustituye dos dependencias: `get_db` (para que todo cuelgue de la transacción que se
    revierte al final) y `get_current_user` (para no tener que firmar un JWT en cada test).
    """

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = _authenticated_as(TEST_USER_ID, TEST_USER_EMAIL)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_for(db_session: Session) -> Generator[Callable[..., TestClient], None, None]:
    """Fábrica de clientes autenticados como distintos usuarios.

    Sirve para probar el aislamiento: `client_for(OTHER_USER_ID)` ve la API exactamente
    como la vería otra persona con su propia sesión.
    """

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    def _make(user_id: UUID, email: str | None = None) -> TestClient:
        app.dependency_overrides[get_current_user] = _authenticated_as(user_id, email)
        return TestClient(app)

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient SIN sesión: `get_current_user` queda intacto y rechaza con 401.

    Con esta fixture se comprueba que los endpoints financieros dejaron de ser públicos.
    """

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
