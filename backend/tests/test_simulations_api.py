"""Tests de integración de /api/v1/simulations. Transaccionales contra PostgreSQL."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import PurchaseSimulation, UserProfile
from tests.conftest import API, TEST_USER_ID, requires_postgres

pytestmark = requires_postgres

TODAY = date.today()


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "purchase_name": "Notebook prueba",
        "total_amount": "900000.00",
        "installments": 9,
        "first_installment_date": (TODAY + timedelta(days=20)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _balance(client: TestClient) -> Decimal:
    return Decimal(client.get(f"{API}/profile").json()["current_balance"])


def _insert_simulation(session: Session, user_id: UUID, name: str, created_at: datetime) -> UUID:
    sim = PurchaseSimulation(
        user_id=user_id,
        purchase_name=name,
        total_amount=Decimal("100000.00"),
        installments=2,
        installment_amount=Decimal("50000.00"),
        first_installment_date=TODAY + timedelta(days=30),
        result={"conclusion": "fits_within_reserves"},
        created_at=created_at,
    )
    session.add(sim)
    session.flush()
    return sim.id


# ---------- POST ----------


def test_crear_simulacion_devuelve_201_y_resultado(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.post(f"{API}/simulations/purchase", json=_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["purchase_name"] == "Notebook prueba"
    assert body["installments"] == 9
    assert body["total_amount"] == "900000.00"
    # El resultado del motor viaja en `result`, con el calendario completo.
    result = body["result"]
    assert len(result["schedule"]) == 9
    total_cuotas = sum(Decimal(item["amount"]) for item in result["schedule"])
    assert total_cuotas == Decimal("900000.00")
    assert "start_next_month" in result
    assert result["conclusion"] in {"fits_within_reserves", "breaks_reserves", "insufficient_data"}


def test_crear_simulacion_guarda_result_jsonb(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    sim_id = client.post(f"{API}/simulations/purchase", json=_payload()).json()["id"]

    stored = db_session.get(PurchaseSimulation, UUID(sim_id))
    assert stored is not None
    assert isinstance(stored.result, dict)
    assert stored.result["installment_count"] == 9
    # No quedan Decimal ni date crudos: el total es string, las fechas ISO.
    assert stored.result["total_purchase_amount"] == "900000.00"
    assert stored.result["schedule"][0]["due_date"].count("-") == 2


def test_crear_simulacion_no_modifica_saldo_ni_entidades(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    client.post(
        f"{API}/commitments",
        json={
            "name": "Alquiler",
            "amount": "250000.00",
            "due_date": (TODAY + timedelta(days=5)).isoformat(),
            "category": "vivienda",
        },
    )
    saldo_antes = _balance(client)
    tx_antes = client.get(f"{API}/transactions").json()
    cm_antes = client.get(f"{API}/commitments").json()

    client.post(f"{API}/simulations/purchase", json=_payload())

    assert _balance(client) == saldo_antes == Decimal("620000.00")
    assert client.get(f"{API}/transactions").json() == tx_antes
    assert client.get(f"{API}/commitments").json() == cm_antes


def test_crear_simulacion_sin_perfil_devuelve_404(client: TestClient) -> None:
    response = client.post(f"{API}/simulations/purchase", json=_payload())
    assert response.status_code == 404
    assert response.json()["detail"] == "Perfil financiero no encontrado"


def test_total_cero_es_422(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    assert (
        client.post(f"{API}/simulations/purchase", json=_payload(total_amount="0")).status_code
        == 422
    )


def test_cuotas_cero_es_422(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    assert (
        client.post(f"{API}/simulations/purchase", json=_payload(installments=0)).status_code == 422
    )


def test_mas_de_24_cuotas_es_422(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    assert (
        client.post(f"{API}/simulations/purchase", json=_payload(installments=25)).status_code
        == 422
    )


def test_fecha_anterior_es_422(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    ayer = (TODAY - timedelta(days=1)).isoformat()
    assert (
        client.post(
            f"{API}/simulations/purchase", json=_payload(first_installment_date=ayer)
        ).status_code
        == 422
    )


# ---------- GET ----------


def test_listar_orden_descendente_por_created_at(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    now = datetime.now(UTC)
    _insert_simulation(db_session, TEST_USER_ID, "vieja", now - timedelta(hours=2))
    _insert_simulation(db_session, TEST_USER_ID, "media", now - timedelta(hours=1))
    _insert_simulation(db_session, TEST_USER_ID, "nueva", now)

    rows = client.get(f"{API}/simulations").json()

    assert [r["purchase_name"] for r in rows] == ["nueva", "media", "vieja"]


def test_listar_maximo_10(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    now = datetime.now(UTC)
    for i in range(11):
        _insert_simulation(db_session, TEST_USER_ID, f"sim-{i:02d}", now - timedelta(hours=i))

    rows = client.get(f"{API}/simulations").json()

    assert len(rows) == 10
    # Las 10 más nuevas; la más vieja (mayor delta de horas) queda afuera.
    assert "sim-10" not in {r["purchase_name"] for r in rows}


def test_listar_solo_perfil_demo(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    otro_id = UUID("55555555-5555-4555-8555-555555555555")
    db_session.add(UserProfile(id=otro_id, name="Otro", current_balance=Decimal("0")))
    db_session.flush()

    now = datetime.now(UTC)
    _insert_simulation(db_session, TEST_USER_ID, "mia", now)
    _insert_simulation(db_session, otro_id, "ajena", now)

    rows = client.get(f"{API}/simulations").json()

    names = {r["purchase_name"] for r in rows}
    assert "mia" in names
    assert "ajena" not in names


def test_listar_vacio_sin_simulaciones(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    assert client.get(f"{API}/simulations").json() == []
