"""Tests de integración de /api/v1/dashboard/summary. Transaccionales contra PostgreSQL."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.core.timezone import app_today
from tests.conftest import API, requires_postgres

pytestmark = requires_postgres

TODAY = app_today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _commitment(client: TestClient, amount: str, due_in_days: int) -> None:
    response = client.post(
        f"{API}/commitments",
        json={
            "name": "compromiso",
            "amount": amount,
            "due_date": _iso(due_in_days),
            "category": "varios",
        },
    )
    assert response.status_code == 201, response.text


def test_summary_sin_perfil_devuelve_404(client: TestClient) -> None:
    response = client.get(f"{API}/dashboard/summary")
    assert response.status_code == 404
    assert response.json()["detail"] == "Perfil financiero no encontrado"


def test_summary_calcula_disponible_real(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    # Próximo ingreso en 10 días; un compromiso de 250000 dentro del horizonte.
    make_profile(next_income_date=_iso(10))
    _commitment(client, "250000.00", 5)

    response = client.get(f"{API}/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    # available_real = 620000 - 250000 - 120000 - 40000 = 210000
    assert body["available_real"] == "210000.00"
    assert body["spendable_total"] == "210000.00"
    assert body["pending_commitments_amount"] == "250000.00"
    assert body["days_until_income"] == 10
    assert body["daily_safe_to_spend"] == "21000.00"  # 210000 / 10
    assert body["status"] == "healthy"
    # El dinero viaja como string.
    assert body["current_balance"] == "620000.00"
    assert isinstance(body["current_balance"], str)


def test_summary_incluye_proyeccion_de_fin_de_mes(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile(next_income_date=_iso(10))

    body = client.get(f"{API}/dashboard/summary").json()

    forecast = body["forecast"]
    assert "projected_month_end_balance" in forecast
    assert "projected_month_end_margin" in forecast
    assert "gastos variables" in forecast["note"]


def test_summary_deficit(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile(
        current_balance="100000.00",
        protected_amount="0",
        safety_buffer="0",
        next_income_date=_iso(10),
    )
    _commitment(client, "250000.00", 3)

    body = client.get(f"{API}/dashboard/summary").json()

    assert body["available_real"] == "-150000.00"
    assert body["spendable_total"] == "0.00"
    assert body["deficit_amount"] == "150000.00"
    assert body["status"] == "deficit"


def test_summary_fecha_de_ingreso_faltante(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile(next_income_date=None)

    body = client.get(f"{API}/dashboard/summary").json()

    assert body["days_until_income"] is None
    assert body["daily_safe_to_spend"] is None
    assert body["status"] == "incomplete"
    assert "No configuraste la fecha de tu próximo ingreso." in body["warnings"]


def test_summary_no_modifica_la_base(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile(next_income_date=_iso(10))
    _commitment(client, "100000.00", 5)

    before = client.get(f"{API}/profile").json()["current_balance"]
    client.get(f"{API}/dashboard/summary")
    client.get(f"{API}/dashboard/summary")
    after = client.get(f"{API}/profile").json()["current_balance"]

    assert Decimal(before) == Decimal(after)
    # El saldo no se toca al leer el dashboard.
    assert Decimal(after) == Decimal("620000.00")
