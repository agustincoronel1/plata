"""Ciclo de vida del gasto que genera un compromiso pagado, y bordes del gráfico.

El pago de un compromiso ya estaba implementado y cubierto (ver `test_commitments_api.py`).
Lo que faltaba fijar es lo de los extremos, que es justo donde se cuela el doble conteo:

- Qué pasa al BORRAR un compromiso ya pagado. La decisión es conservar el gasto: la plata
  salió de la cuenta de verdad, y borrarlo en silencio le devolvería saldo a la persona por
  una operación que solo tocaba una agenda. La FK es `ON DELETE SET NULL`, así que el
  movimiento queda como uno manual más.
- Que el gráfico de categorías no se rompa con datos viejos: categorías fuera del
  vocabulario actual, o registros anteriores a la normalización.

Todo se mide de punta a punta contra PostgreSQL, porque el punto es exactamente cómo
interactúan la constraint, el saldo y las consultas agregadas.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import API, TEST_USER_ID, requires_postgres

pytestmark = requires_postgres


def _commitment(client: TestClient, **overrides: object) -> dict:
    payload = {
        "name": "Factura de luz",
        "amount": "50000.00",
        "due_date": "2026-08-20",
        "category": "servicios",
    }
    payload.update(overrides)
    response = client.post(f"{API}/commitments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _pay(client: TestClient, commitment_id: str) -> None:
    response = client.patch(f"{API}/commitments/{commitment_id}", json={"status": "paid"})
    assert response.status_code == 200, response.text


def _balance(client: TestClient) -> Decimal:
    return Decimal(client.get(f"{API}/profile").json()["current_balance"])


def _summary(client: TestClient) -> dict:
    return client.get(f"{API}/dashboard/summary").json()


# ---------- 7. La categoría del compromiso llega a la transacción ----------


def test_la_categoria_del_compromiso_viaja_al_gasto(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    commitment = _commitment(client, category="salud", name="Prepaga")

    _pay(client, commitment["id"])

    gasto = client.get(f"{API}/transactions").json()[0]
    assert gasto["category"] == "salud"
    assert gasto["description"] == "Prepaga"
    assert gasto["commitment_id"] == commitment["id"]


def test_el_gasto_del_compromiso_entra_en_el_grafico_de_categorias(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """El gráfico tiene que reflejar lo que se pagó desde compromisos, no solo lo manual."""
    make_profile()
    _pay(client, _commitment(client, category="servicios", amount="50000.00")["id"])

    resumen = _summary(client)["category_summary"]

    assert [(i["category"], Decimal(i["amount"])) for i in resumen] == [
        ("servicios", Decimal("50000.00"))
    ]


def test_el_gasto_del_compromiso_se_cuenta_una_sola_vez_en_el_grafico(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Pagar dos veces no puede inflar la categoría."""
    make_profile()
    commitment = _commitment(client, category="servicios", amount="50000.00")
    _pay(client, commitment["id"])
    _pay(client, commitment["id"])

    resumen = _summary(client)["category_summary"]

    assert len(resumen) == 1
    assert Decimal(resumen[0]["amount"]) == Decimal("50000.00")


# ---------- Desmarcar ----------


def test_desmarcar_un_pago_borra_su_gasto_y_devuelve_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Volver a pendiente deshace el pago entero: no quedan restos."""
    make_profile(current_balance="500000.00")
    commitment = _commitment(client, amount="50000.00")
    _pay(client, commitment["id"])
    assert _balance(client) == Decimal("450000.00")

    client.patch(f"{API}/commitments/{commitment['id']}", json={"status": "pending"})

    assert client.get(f"{API}/transactions").json() == []
    assert _balance(client) == Decimal("500000.00")
    assert _summary(client)["category_summary"] == []


def test_re_pagar_despues_de_desmarcar_no_acumula(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile(current_balance="500000.00")
    commitment = _commitment(client, amount="50000.00")

    for status in ("paid", "pending", "paid"):
        client.patch(f"{API}/commitments/{commitment['id']}", json={"status": status})

    assert len(client.get(f"{API}/transactions").json()) == 1
    assert _balance(client) == Decimal("450000.00")


# ---------- Borrar un compromiso pagado ----------


def test_borrar_un_compromiso_pagado_conserva_el_gasto(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Decisión explícita: el gasto sobrevive y el saldo NO se devuelve.

    La plata salió de la cuenta cuando se pagó. Borrar el compromiso es sacar algo de la
    agenda, no deshacer el pago; devolver el saldo ahí le inventaría plata a la persona.
    El movimiento queda como uno manual más (su `commitment_id` pasa a NULL por la FK).
    """
    make_profile(current_balance="500000.00")
    commitment = _commitment(client, amount="50000.00")
    _pay(client, commitment["id"])

    assert client.delete(f"{API}/commitments/{commitment['id']}").status_code == 204

    movimientos = client.get(f"{API}/transactions").json()
    assert len(movimientos) == 1
    assert movimientos[0]["commitment_id"] is None
    assert Decimal(movimientos[0]["amount"]) == Decimal("50000.00")
    assert _balance(client) == Decimal("450000.00")


def test_el_gasto_huerfano_sigue_contando_una_sola_vez(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Tras borrar el compromiso, el gasto no se duplica ni desaparece del gráfico."""
    make_profile(current_balance="500000.00")
    commitment = _commitment(client, amount="50000.00", category="servicios")
    _pay(client, commitment["id"])
    client.delete(f"{API}/commitments/{commitment['id']}")

    resumen = _summary(client)

    assert Decimal(resumen["month_expenses_total"]) == Decimal("50000.00")
    assert len(resumen["category_summary"]) == 1
    assert Decimal(resumen["category_summary"][0]["amount"]) == Decimal("50000.00")
    # Y ya no cuenta como compromiso pendiente: si contara, sería doble conteo.
    assert Decimal(resumen["pending_commitments_amount"]) == Decimal("0.00")


def test_borrar_un_compromiso_pendiente_no_deja_ningun_gasto(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Sin pagar no hubo movimiento de plata, así que no hay nada que conservar."""
    make_profile(current_balance="500000.00")
    commitment = _commitment(client)

    client.delete(f"{API}/commitments/{commitment['id']}")

    assert client.get(f"{API}/transactions").json() == []
    assert _balance(client) == Decimal("500000.00")


# ---------- 10 y 11. Datos viejos y categorías desconocidas ----------


def _insert_legacy_expense(session: Session, category: str, amount: str) -> None:
    """Inserta un gasto salteando los schemas, como los que ya viven en producción."""
    session.execute(
        text(
            "INSERT INTO transactions "
            "(id, user_id, type, amount, currency, category, occurred_on, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :u, 'expense', :a, 'ARS', :c, current_date, now(), now())"
        ),
        {"u": TEST_USER_ID, "a": amount, "c": category},
    )
    session.flush()


def test_una_categoria_desconocida_no_rompe_el_grafico(
    client: TestClient, make_profile: Callable[..., dict], db_session: Session
) -> None:
    """Un valor fuera del vocabulario actual se muestra tal cual, sin tirar el resumen.

    Es el caso de los registros anteriores a la normalización: el gráfico tiene que
    seguir apareciendo, no desaparecer entero por una fila rara.
    """
    make_profile()
    _insert_legacy_expense(db_session, "gastronomia_2024", "1000.00")

    resumen = _summary(client)["category_summary"]

    assert [i["category"] for i in resumen] == ["gastronomia_2024"]
    assert Decimal(resumen[0]["amount"]) == Decimal("1000.00")


def test_una_categoria_desconocida_convive_con_las_actuales(
    client: TestClient, make_profile: Callable[..., dict], db_session: Session
) -> None:
    """Una sola fila desconocida no puede tapar ni descontar a las demás."""
    make_profile()
    client.post(
        f"{API}/transactions",
        json={"type": "expense", "amount": "3000.00", "category": "comida"},
    )
    _insert_legacy_expense(db_session, "categoria_rarisima", "1000.00")

    resumen = _summary(client)["category_summary"]
    total = sum(Decimal(i["amount"]) for i in resumen)

    assert {i["category"] for i in resumen} == {"comida", "categoria_rarisima"}
    assert total == Decimal("4000.00")
    assert sum(Decimal(i["percentage"]) for i in resumen) == Decimal("100.0")


def test_sin_gastos_el_grafico_queda_vacio_y_no_falla(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """El estado vacío es una lista vacía: el frontend muestra su propio mensaje."""
    make_profile()

    resumen = _summary(client)

    assert resumen["category_summary"] == []
    assert Decimal(resumen["month_expenses_total"]) == Decimal("0.00")


def test_un_ingreso_no_entra_en_el_grafico_de_gastos(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Los ingresos conservan categoría de texto libre y no son "en qué se fue tu plata"."""
    make_profile()
    client.post(
        f"{API}/transactions",
        json={"type": "income", "amount": "100000.00", "category": "sueldo"},
    )

    assert _summary(client)["category_summary"] == []
