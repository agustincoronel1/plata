"""Tests de integración de /api/v1/transactions y la política de saldo.

Transaccionales contra PostgreSQL: cada test parte de un perfil demo con saldo 620000 y
todo se revierte al terminar.
"""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionType, UserProfile
from app.schemas.transaction import TransactionCreate
from app.services import transaction_service
from app.services.exceptions import NotFoundError
from tests.conftest import API, TEST_USER_ID, requires_postgres

pytestmark = requires_postgres

TODAY = date.today()


def _expense(amount: str, **extra: object) -> dict[str, object]:
    return {"type": "expense", "amount": amount, "category": "comida", **extra}


def _income(amount: str, **extra: object) -> dict[str, object]:
    return {"type": "income", "amount": amount, "category": "sueldo", **extra}


def _balance(client: TestClient) -> Decimal:
    return Decimal(client.get(f"{API}/profile").json()["current_balance"])


# ---------- Alta y efecto sobre el saldo ----------


def test_crear_income_aumenta_saldo(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()

    response = client.post(f"{API}/transactions", json=_income("50000.00"))

    assert response.status_code == 201
    assert response.json()["type"] == "income"
    assert _balance(client) == Decimal("670000.00")


def test_crear_expense_reduce_saldo(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()

    response = client.post(f"{API}/transactions", json=_expense("20000.00"))

    assert response.status_code == 201
    assert _balance(client) == Decimal("600000.00")


def test_crear_movimiento_sin_perfil_devuelve_404(client: TestClient) -> None:
    response = client.post(f"{API}/transactions", json=_expense("20000.00"))

    assert response.status_code == 404
    assert response.json()["detail"] == "Perfil financiero no encontrado"


def test_crear_movimiento_amount_cero_es_422(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.post(f"{API}/transactions", json=_expense("0"))

    assert response.status_code == 422


# ---------- Edición ----------


def test_editar_monto_de_expense_ajusta_la_diferencia(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/transactions", json=_expense("20000.00")).json()
    assert _balance(client) == Decimal("600000.00")

    response = client.patch(f"{API}/transactions/{created['id']}", json={"amount": "30000.00"})

    assert response.status_code == 200
    # 620000 - 30000: se aplica la diferencia, no se suma dos veces.
    assert _balance(client) == Decimal("590000.00")


def test_cambiar_expense_a_income_recalcula_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/transactions", json=_expense("20000.00")).json()
    assert _balance(client) == Decimal("600000.00")

    response = client.patch(f"{API}/transactions/{created['id']}", json={"type": "income"})

    assert response.status_code == 200
    # Se revierte el -20000 y se aplica +20000: 620000 + 20000.
    assert _balance(client) == Decimal("640000.00")


def test_patch_vacio_es_422(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    created = client.post(f"{API}/transactions", json=_expense("20000.00")).json()

    response = client.patch(f"{API}/transactions/{created['id']}", json={})

    assert response.status_code == 422


def test_editar_movimiento_inexistente_es_404(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.patch(f"{API}/transactions/{uuid4()}", json={"amount": "10"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Movimiento no encontrado"


# ---------- Eliminación ----------


def test_eliminar_movimiento_restaura_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/transactions", json=_expense("20000.00")).json()
    assert _balance(client) == Decimal("600000.00")

    response = client.delete(f"{API}/transactions/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert _balance(client) == Decimal("620000.00")


def test_eliminar_income_restaura_el_saldo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/transactions", json=_income("50000.00")).json()
    assert _balance(client) == Decimal("670000.00")

    client.delete(f"{API}/transactions/{created['id']}")

    assert _balance(client) == Decimal("620000.00")


def test_eliminar_movimiento_inexistente_es_404(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.delete(f"{API}/transactions/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Movimiento no encontrado"


# ---------- Listado ----------


def test_listado_ordenado_por_fecha_descendente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    anteayer = str(TODAY - timedelta(days=2))
    ayer = str(TODAY - timedelta(days=1))
    client.post(f"{API}/transactions", json=_expense("100", occurred_on=anteayer))
    client.post(f"{API}/transactions", json=_expense("200", occurred_on=str(TODAY)))
    client.post(f"{API}/transactions", json=_expense("300", occurred_on=ayer))

    rows = client.get(f"{API}/transactions").json()

    fechas = [row["occurred_on"] for row in rows]
    assert fechas == sorted(fechas, reverse=True)


def test_listar_no_modifica_el_saldo(client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    client.post(f"{API}/transactions", json=_expense("20000.00"))
    saldo = _balance(client)

    client.get(f"{API}/transactions")
    client.get(f"{API}/transactions")

    assert _balance(client) == saldo


# ---------- Aislamiento por perfil ----------


def test_no_se_puede_editar_movimiento_de_otro_perfil(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    otro_id = UUID("44444444-4444-4444-8444-444444444444")
    otra_tx_id = uuid4()
    db_session.add(UserProfile(id=otro_id, name="Otro", current_balance=Decimal("0")))
    db_session.flush()
    db_session.add(
        Transaction(
            id=otra_tx_id,
            user_id=otro_id,
            type=TransactionType.EXPENSE,
            amount=Decimal("5000"),
            category="otra",
            occurred_on=TODAY,
        )
    )
    db_session.flush()

    patch = client.patch(f"{API}/transactions/{otra_tx_id}", json={"amount": "1"})
    delete = client.delete(f"{API}/transactions/{otra_tx_id}")

    assert patch.status_code == 404
    assert delete.status_code == 404


# ---------- Atomicidad ----------


def test_error_durante_la_operacion_hace_rollback(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el commit falla, ni el movimiento ni el cambio de saldo quedan aplicados."""
    from app.schemas.profile import ProfileUpdate
    from app.services import profile_service

    perfil = profile_service.upsert_profile(
        db_session,
        TEST_USER_ID,
        ProfileUpdate(name="Demo", current_balance=Decimal("620000.00")),
    )
    saldo_original = perfil.current_balance

    def commit_roto() -> None:
        raise SQLAlchemyError("falla simulada en commit")

    monkeypatch.setattr(db_session, "commit", commit_roto)

    payload = TransactionCreate(
        type=TransactionType.EXPENSE, amount=Decimal("20000"), category="comida"
    )
    with pytest.raises(SQLAlchemyError):
        transaction_service.create_transaction(db_session, TEST_USER_ID, payload)

    db_session.refresh(perfil)
    assert perfil.current_balance == saldo_original
    assert db_session.query(Transaction).count() == 0


def test_error_de_dominio_no_filtra_detalles_tecnicos(client: TestClient) -> None:
    """Un 404 lleva solo el mensaje de dominio, sin SQL ni internals."""
    body = client.delete(f"{API}/transactions/{uuid4()}").text.lower()

    for filtrado in ("traceback", "sqlalchemy", "psycopg", "select", "user_id", "postgresql"):
        assert filtrado not in body


def test_notfounderror_es_una_excepcion_simple() -> None:
    """La excepción de dominio no envuelve errores internos."""
    error = NotFoundError("Movimiento no encontrado")
    assert str(error) == "Movimiento no encontrado"
