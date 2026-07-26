"""Tests de /health/db con sesiones falsas.

No tocan PostgreSQL: la comprobación real contra la base se hace a mano con el
contenedor levantado.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.main import app

client = TestClient(app)


class FakeSession:
    """Sesión que responde a execute() como lo haría PostgreSQL disponible."""

    def execute(self, statement: object) -> object:
        return object()


class BrokenSession:
    """Sesión que falla al ejecutar, como con PostgreSQL detenido."""

    def execute(self, statement: object) -> object:
        raise OperationalError("SELECT 1", None, Exception("connection refused"))


@pytest.fixture(autouse=True)
def clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def test_health_db_ok() -> None:
    app.dependency_overrides[get_db] = lambda: FakeSession()

    response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_health_db_unavailable() -> None:
    app.dependency_overrides[get_db] = lambda: BrokenSession()

    response = client.get("/health/db")

    assert response.status_code == 503


def test_health_db_unavailable_no_filtra_detalles() -> None:
    app.dependency_overrides[get_db] = lambda: BrokenSession()

    body = client.get("/health/db").text.lower()

    for filtrado in ("postgresql+psycopg", "password", "5432", "traceback", "connection refused"):
        assert filtrado not in body


def test_health_no_depende_de_la_base() -> None:
    """/health responde aunque get_db esté roto."""
    app.dependency_overrides[get_db] = lambda: BrokenSession()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
