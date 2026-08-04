"""Tests de integración de /api/v1/profile. Transaccionales contra PostgreSQL."""

from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.conftest import (
    API,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

pytestmark = requires_postgres


def test_get_profile_sin_perfil_devuelve_404(client: TestClient) -> None:
    response = client.get(f"{API}/profile")

    assert response.status_code == 404
    assert response.json()["detail"] == "Perfil financiero no encontrado"


def test_put_profile_crea_perfil_y_responde_200(client: TestClient) -> None:
    response = client.put(f"{API}/profile", json=default_profile_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(TEST_USER_ID)
    assert body["name"] == "Agustín Demo"
    assert body["currency"] == "ARS"
    # El dinero viaja como string y conserva la precisión.
    assert body["current_balance"] == "620000.00"
    assert "created_at" in body and "updated_at" in body


def test_get_profile_devuelve_el_perfil_creado(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.get(f"{API}/profile")

    assert response.status_code == 200
    assert response.json()["current_balance"] == "620000.00"


def test_put_profile_actualiza_perfil_existente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.put(
        f"{API}/profile",
        json=default_profile_payload(name="Otro Nombre", current_balance="-1000.00"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Otro Nombre"
    # current_balance admite negativos.
    assert Decimal(body["current_balance"]) == Decimal("-1000.00")
    assert body["id"] == str(TEST_USER_ID)


def test_put_profile_rechaza_moneda_no_ars(client: TestClient) -> None:
    response = client.put(f"{API}/profile", json=default_profile_payload(currency="USD"))

    assert response.status_code == 422


def test_put_profile_ignora_id_del_cliente(client: TestClient) -> None:
    payload = default_profile_payload()
    payload["id"] = "99999999-9999-4999-8999-999999999999"

    response = client.put(f"{API}/profile", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] == str(TEST_USER_ID)
