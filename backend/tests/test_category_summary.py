"""Resumen de gastos por categoría del dashboard.

Dos niveles: el armado del top 5 + "otros" (puro, sin base) y el endpoint completo
(`/api/v1/dashboard/summary`) contra PostgreSQL, incluido el aislamiento por usuario.
"""

from collections.abc import Callable, Generator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockAIProvider
from app.main import app
from app.services.dashboard_service import build_category_summary
from app.services.draft_store import InMemoryDraftStore, get_draft_store
from tests.conftest import (
    API,
    OTHER_USER_ID,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

TODAY = date.today()
MONTH_START = TODAY.replace(day=1)


# ---------- Armado del resumen (sin base de datos) ----------


def _amounts(items: list[dict]) -> list[tuple[str, str, str]]:
    return [(i["category"], str(i["amount"]), str(i["percentage"])) for i in items]


def test_sin_gastos_devuelve_lista_vacia() -> None:
    assert build_category_summary({}) == []


def test_ordena_de_mayor_a_menor_y_calcula_porcentaje() -> None:
    resumen = build_category_summary(
        {
            "comida": Decimal("25000.00"),
            "transporte": Decimal("50000.00"),
            "ocio": Decimal("25000.00"),
        }
    )
    assert _amounts(resumen) == [
        ("transporte", "50000.00", "50.0"),
        ("comida", "25000.00", "25.0"),
        ("ocio", "25000.00", "25.0"),
    ]
    assert sum(item["percentage"] for item in resumen) == Decimal("100.0")


def test_top_cinco_y_el_resto_agrupado_en_otros() -> None:
    expenses = {
        "transporte": Decimal("600.00"),
        "comida": Decimal("500.00"),
        "vivienda": Decimal("400.00"),
        "servicios": Decimal("300.00"),
        "salud": Decimal("200.00"),
        "ocio": Decimal("60.00"),
        "compras": Decimal("40.00"),
    }
    resumen = build_category_summary(expenses)

    assert len(resumen) == 6
    assert [item["category"] for item in resumen[:5]] == [
        "transporte",
        "comida",
        "vivienda",
        "servicios",
        "salud",
    ]
    # Las dos categorías más chicas se suman en "otros" (60 + 40).
    assert resumen[-1] == {
        "category": "otros",
        "amount": Decimal("100.00"),
        "percentage": Decimal("4.8"),
    }
    assert sum(item["amount"] for item in resumen) == sum(expenses.values())


def test_otros_ya_existente_absorbe_al_resto() -> None:
    expenses = {
        "transporte": Decimal("600.00"),
        "comida": Decimal("500.00"),
        "vivienda": Decimal("400.00"),
        "servicios": Decimal("300.00"),
        "otros": Decimal("200.00"),
        "ocio": Decimal("100.00"),
    }
    resumen = build_category_summary(expenses)

    assert len(resumen) == 5
    otros = next(item for item in resumen if item["category"] == "otros")
    assert otros["amount"] == Decimal("300.00")


# ---------- Endpoint completo (integración, se omite sin PostgreSQL) ----------


@pytest.fixture
def ai_client(client: TestClient) -> Generator[TestClient, None, None]:
    store = InMemoryDraftStore()
    app.dependency_overrides[get_draft_store] = lambda: store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


def _expense(client: TestClient, amount: str, *, category: str | None = None, **extra) -> dict:
    payload: dict[str, object] = {
        "type": "expense",
        "amount": amount,
        "occurred_on": TODAY.isoformat(),
        **extra,
    }
    if category is not None:
        payload["category"] = category
    response = client.post(f"{API}/transactions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _summary(client: TestClient) -> dict:
    response = client.get(f"{API}/dashboard/summary")
    assert response.status_code == 200, response.text
    return response.json()


@requires_postgres
def test_sin_gastos_el_resumen_esta_vacio(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _summary(client)

    assert body["category_summary"] == []
    assert body["month_expenses_total"] == "0.00"
    assert body["month_income_total"] == "0.00"
    assert body["month_savings"] == "0.00"


@requires_postgres
def test_agrupa_los_gastos_del_mes_por_categoria(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _expense(client, "30000.00", category="transporte")
    _expense(client, "10000.00", category="transporte")
    _expense(client, "10000.00", category="comida")

    body = _summary(client)

    assert body["category_summary"] == [
        {"category": "transporte", "amount": "40000.00", "percentage": "80.0"},
        {"category": "comida", "amount": "10000.00", "percentage": "20.0"},
    ]
    assert body["month_expenses_total"] == "50000.00"


@requires_postgres
def test_solo_incluye_gastos_y_del_mes_en_curso(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _expense(client, "10000.00", category="comida")
    client.post(
        f"{API}/transactions",
        json={
            "type": "income",
            "amount": "500000.00",
            "category": "sueldo",
            "occurred_on": TODAY.isoformat(),
        },
    )
    # Un gasto del mes anterior no entra en el resumen del mes en curso.
    previous = MONTH_START - timedelta(days=1)
    client.post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "77000.00",
            "category": "ocio",
            "occurred_on": previous.isoformat(),
        },
    )

    body = _summary(client)

    assert [item["category"] for item in body["category_summary"]] == ["comida"]
    assert body["month_expenses_total"] == "10000.00"
    assert body["month_income_total"] == "500000.00"
    assert body["month_savings"] == "490000.00"
    assert body["previous_month_expenses_total"] == "77000.00"


@requires_postgres
def test_el_resumen_no_mezcla_usuarios(client_for: Callable[..., TestClient]) -> None:
    # `client_for` sustituye la identidad autenticada en cada llamada: hay que pedir el
    # cliente justo antes de cada petición para que cada una vaya a nombre de su dueño.
    for user_id in (TEST_USER_ID, OTHER_USER_ID):
        response = client_for(user_id).put(f"{API}/profile", json=default_profile_payload())
        assert response.status_code == 200, response.text

    _expense(client_for(TEST_USER_ID), "40000.00", category="transporte")
    _expense(client_for(OTHER_USER_ID), "9000.00", category="ocio")

    assert _summary(client_for(TEST_USER_ID))["category_summary"] == [
        {"category": "transporte", "amount": "40000.00", "percentage": "100.0"}
    ]
    assert _summary(client_for(OTHER_USER_ID))["category_summary"] == [
        {"category": "ocio", "amount": "9000.00", "percentage": "100.0"}
    ]


@requires_postgres
def test_el_alta_manual_clasifica_sola(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    creado = _expense(client, "15000.00", description="Nafta")
    assert creado["category"] == "transporte"

    explicito = _expense(client, "15000.00", category="ocio", description="Nafta")
    assert explicito["category"] == "ocio"

    assert {item["category"] for item in _summary(client)["category_summary"]} == {
        "transporte",
        "ocio",
    }


@requires_postgres
def test_el_alta_por_ia_guarda_la_categoria(
    ai_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    borrador = ai_client.post(
        f"{API}/ai/transactions/parse",
        json={"text": "Gasté 25 lucas ayer en nafta con débito"},
    ).json()

    assert borrador["transaction"]["category"] == "transporte"
    assert "category" not in borrador["missing_fields"]

    confirmado = ai_client.post(
        f"{API}/ai/transactions/{borrador['draft_id']}/confirm", json={"confirmed": True}
    )
    assert confirmado.status_code == 201, confirmado.text
    assert confirmado.json()["transaction"]["category"] == "transporte"
