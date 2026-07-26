"""Tests de integración de /api/v1/commitments. Transaccionales contra PostgreSQL.

Regla central del Día 2 que estos tests fijan: los compromisos NO tocan el saldo, ni al
crearse, ni al editarse, ni al marcarse pagados o cancelados.
"""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import API, requires_postgres

pytestmark = requires_postgres

TODAY = date.today()


def _commitment(name: str, amount: str, due_in_days: int, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "amount": amount,
        "due_date": str(TODAY + timedelta(days=due_in_days)),
        "category": "vivienda",
        **extra,
    }


def _balance(client: TestClient) -> Decimal:
    return Decimal(client.get(f"{API}/profile").json()["current_balance"])


def test_crear_compromiso_nace_pending(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    response = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["name"] == "Alquiler"


def test_crear_compromiso_ignora_status_del_cliente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    payload = _commitment("Alquiler", "250000.00", 5)
    payload["status"] = "paid"

    body = client.post(f"{API}/commitments", json=payload).json()

    assert body["status"] == "pending"


def test_crear_compromiso_sin_perfil_devuelve_404(client: TestClient) -> None:
    response = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5))

    assert response.status_code == 404
    assert response.json()["detail"] == "Perfil financiero no encontrado"


def test_crear_compromiso_no_modifica_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5))

    assert _balance(client) == Decimal("620000.00")


def test_listado_ordenado_pending_primero_por_vencimiento(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    lejano = client.post(f"{API}/commitments", json=_commitment("Lejano", "100", 15)).json()
    cercano = client.post(f"{API}/commitments", json=_commitment("Cercano", "100", 3)).json()
    medio = client.post(f"{API}/commitments", json=_commitment("Medio", "100", 8)).json()
    # Uno pagado y uno cancelado deben quedar después de los pending.
    pagado = client.post(f"{API}/commitments", json=_commitment("Pagado", "100", 1)).json()
    client.patch(f"{API}/commitments/{pagado['id']}", json={"status": "paid"})
    cancelado = client.post(f"{API}/commitments", json=_commitment("Cancelado", "100", 2)).json()
    client.patch(f"{API}/commitments/{cancelado['id']}", json={"status": "cancelled"})

    rows = client.get(f"{API}/commitments").json()
    ids = [row["id"] for row in rows]

    # Pending primero, por due_date ascendente; pagado/cancelado al final.
    assert ids[:3] == [cercano["id"], medio["id"], lejano["id"]]
    assert set(ids[3:]) == {pagado["id"], cancelado["id"]}
    assert all(rows[i]["status"] == "pending" for i in range(3))


def test_editar_compromiso(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    response = client.patch(
        f"{API}/commitments/{created['id']}", json={"amount": "300000.00", "name": "Alquiler nuevo"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == "300000.00"
    assert body["name"] == "Alquiler nuevo"


def test_marcar_compromiso_pagado_no_modifica_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    # El saldo no se toca: la política del Día 2 lo prohíbe explícitamente.
    assert _balance(client) == Decimal("620000.00")
    # Tampoco se creó una transacción.
    assert client.get(f"{API}/transactions").json() == []


def test_marcar_compromiso_cancelado_no_modifica_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "cancelled"})

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert _balance(client) == Decimal("620000.00")


def test_volver_a_pending(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()
    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "pending"})

    assert response.json()["status"] == "pending"


def test_eliminar_compromiso(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    response = client.delete(f"{API}/commitments/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"{API}/commitments").json() == []


def test_editar_compromiso_inexistente_es_404(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.patch(f"{API}/commitments/{uuid4()}", json={"amount": "1"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Compromiso no encontrado"


def test_eliminar_compromiso_inexistente_es_404(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.delete(f"{API}/commitments/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Compromiso no encontrado"


def test_patch_compromiso_vacio_es_422(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    response = client.patch(f"{API}/commitments/{created['id']}", json={})

    assert response.status_code == 422
