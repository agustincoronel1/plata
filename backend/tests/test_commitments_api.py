"""Tests de integración de /api/v1/commitments. Transaccionales contra PostgreSQL.

Un compromiso pendiente no toca el saldo. Al pasar a pagado crea un gasto real vinculado;
al volver desde pagado se elimina solo ese gasto autogenerado.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.timezone import app_today
from app.models import Commitment, CommitmentStatus, Transaction, UserProfile
from app.schemas.commitment import CommitmentCreate, CommitmentUpdate
from app.services import commitment_service, transaction_service
from tests.conftest import API, OTHER_USER_ID, TEST_USER_ID, requires_postgres

pytestmark = requires_postgres

TODAY = app_today()


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


def _transactions(client: TestClient) -> list[dict]:
    return client.get(f"{API}/transactions").json()


def test_crear_compromiso_nace_pending(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    response = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["name"] == "Alquiler"


def test_crear_compromiso_con_categoria_explicita(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    response = client.post(
        f"{API}/commitments",
        json=_commitment("Prepaga", "80000.00", 5, category="salud"),
    )

    assert response.status_code == 201
    assert response.json()["category"] == "salud"


def test_crear_compromiso_sin_categoria_la_sugiere_por_nombre(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    payload = _commitment("Internet", "30000.00", 5)
    payload.pop("category")

    response = client.post(f"{API}/commitments", json=payload)

    assert response.status_code == 201
    assert response.json()["category"] == "servicios"


def test_crear_compromiso_sin_categoria_reconocida_cae_en_otros(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    payload = _commitment("Pago misterioso", "1000.00", 5)
    payload.pop("category")

    response = client.post(f"{API}/commitments", json=payload)

    assert response.status_code == 201
    assert response.json()["category"] == "otros"


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


def test_editar_categoria_de_compromiso_pendiente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Curso", "50000.00", 5)).json()

    response = client.patch(f"{API}/commitments/{created['id']}", json={"category": "educacion"})

    assert response.status_code == 200
    assert response.json()["category"] == "educación"


def test_usuario_no_puede_editar_ni_pagar_compromiso_ajeno(
    client_for: Callable[..., TestClient],
) -> None:
    client_for(TEST_USER_ID).put(
        f"{API}/profile", json={"name": "Alice", "current_balance": "100000"}
    )
    client_for(OTHER_USER_ID).put(
        f"{API}/profile", json={"name": "Bob", "current_balance": "100000"}
    )
    created = (
        client_for(OTHER_USER_ID)
        .post(
            f"{API}/commitments", json=_commitment("Internet", "30000.00", 5, category="servicios")
        )
        .json()
    )

    alice = client_for(TEST_USER_ID)
    edit = alice.patch(f"{API}/commitments/{created['id']}", json={"category": "ocio"})
    alice = client_for(TEST_USER_ID)
    pay = alice.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    assert edit.status_code == 404
    assert pay.status_code == 404
    assert client_for(TEST_USER_ID).get(f"{API}/transactions").json() == []


def test_marcar_compromiso_pagado_crea_gasto_real(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(
        f"{API}/commitments",
        json=_commitment("Internet", "30000.00", 5, category="servicios"),
    ).json()

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert _balance(client) == Decimal("590000.00")
    transactions = _transactions(client)
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx["user_id"] == str(TEST_USER_ID)
    assert tx["type"] == "expense"
    assert tx["amount"] == "30000.00"
    assert tx["currency"] == "ARS"
    assert tx["description"] == "Internet"
    assert tx["category"] == "servicios"
    assert tx["occurred_on"] == str(app_today())
    assert tx["commitment_id"] == created["id"]


def test_pago_cerca_de_medianoche_utc_guarda_fecha_de_argentina(
    client: TestClient, make_profile: Callable[..., dict], monkeypatch
) -> None:
    make_profile()
    utc_near_midnight = datetime(2026, 8, 4, 1, 30, tzinfo=UTC)
    argentina_day = app_today(utc_near_midnight)
    assert argentina_day == date(2026, 8, 3)
    monkeypatch.setattr(commitment_service, "app_today", lambda: argentina_day)
    created = client.post(
        f"{API}/commitments",
        json=_commitment("Internet", "30000.00", 5, category="servicios"),
    ).json()

    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    [transaction] = _transactions(client)
    assert transaction["occurred_on"] == "2026-08-03"


def test_gasto_de_compromiso_aparece_en_endpoint_de_movimientos(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    assert any(tx["commitment_id"] == created["id"] for tx in _transactions(client))


def test_volver_a_marcar_pagado_no_duplica_movimiento(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    first = client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})
    second = client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(_transactions(client)) == 1
    assert _balance(client) == Decimal("370000.00")


def test_pago_usa_la_moneda_del_perfil(
    client: TestClient, db_session: Session, make_profile: Callable[..., dict]
) -> None:
    """`currency` sale del perfil, no de una constante.

    Hoy la API solo acepta ARS al guardar el perfil, así que la moneda se cambia en la
    base: lo que se prueba es que el gasto la lee del perfil y no la tiene escrita a mano.
    Nunca puede quedar NULL, la columna no lo admite.
    """
    make_profile()
    profile = db_session.get(UserProfile, TEST_USER_ID)
    profile.currency = "USD"
    db_session.flush()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    [transaction] = _transactions(client)
    assert transaction["currency"] == "USD"


def test_editar_compromiso_pagado_sincroniza_su_gasto_sin_duplicar(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Política explícita: el gasto autogenerado lo manda el compromiso mientras siga pagado."""
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()
    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})
    [original] = _transactions(client)

    response = client.patch(
        f"{API}/commitments/{created['id']}",
        json={"name": "Alquiler nuevo", "amount": "300000.00", "category": "vivienda"},
    )

    assert response.status_code == 200
    transactions = _transactions(client)
    assert len(transactions) == 1
    assert transactions[0]["id"] == original["id"]
    assert transactions[0]["description"] == "Alquiler nuevo"
    assert transactions[0]["amount"] == "300000.00"
    # El saldo sigue la diferencia, no se resta dos veces.
    assert _balance(client) == Decimal("320000.00")


def test_editar_solo_el_vencimiento_de_un_compromiso_pagado_no_reescribe_el_movimiento(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Solo name/amount/category se reflejan en el gasto. Lo demás no lo toca."""
    make_profile()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()
    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})
    [generated] = _transactions(client)
    client.patch(f"{API}/transactions/{generated['id']}", json={"description": "Pagado a mano"})

    response = client.patch(
        f"{API}/commitments/{created['id']}",
        json={"due_date": str(TODAY + timedelta(days=30))},
    )

    assert response.status_code == 200
    [transaction] = _transactions(client)
    assert transaction["description"] == "Pagado a mano"
    assert _balance(client) == Decimal("370000.00")


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
    assert len(_transactions(client)) == 1
    assert _balance(client) == Decimal("370000.00")

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "pending"})

    assert response.json()["status"] == "pending"
    assert _transactions(client) == []
    assert _balance(client) == Decimal("620000.00")


def test_reversion_no_elimina_movimiento_manual(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    manual = client.post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "1000.00",
            "category": "ocio",
            "description": "Manual",
        },
    ).json()
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()
    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})

    response = client.patch(f"{API}/commitments/{created['id']}", json={"status": "pending"})

    assert response.status_code == 200
    remaining = _transactions(client)
    assert [tx["id"] for tx in remaining] == [manual["id"]]
    assert remaining[0]["commitment_id"] is None
    assert _balance(client) == Decimal("619000.00")


def test_compromiso_pagado_deja_de_contar_como_pendiente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile(next_income_date=str(TODAY + timedelta(days=10)))
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()

    before = client.get(f"{API}/dashboard/summary").json()
    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})
    after = client.get(f"{API}/dashboard/summary").json()

    assert before["pending_commitments_amount"] == "250000.00"
    assert after["pending_commitments_amount"] == "0.00"


def test_dashboard_no_descuenta_compromiso_y_gasto_dos_veces(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile(next_income_date=str(TODAY + timedelta(days=10)))
    created = client.post(f"{API}/commitments", json=_commitment("Alquiler", "250000.00", 5)).json()
    before = client.get(f"{API}/dashboard/summary").json()

    client.patch(f"{API}/commitments/{created['id']}", json={"status": "paid"})
    after = client.get(f"{API}/dashboard/summary").json()

    assert before["available_real"] == "210000.00"
    assert after["available_real"] == "210000.00"
    assert after["month_expenses_total"] == "250000.00"
    assert after["category_summary"] == [
        {"category": "vivienda", "amount": "250000.00", "percentage": "100.0"}
    ]


def test_error_creando_movimiento_deja_compromiso_pendiente(
    db_session: Session, monkeypatch
) -> None:
    db_session.add(UserProfile(id=TEST_USER_ID, name="Demo", current_balance=Decimal("620000.00")))
    db_session.flush()
    commitment = commitment_service.create_commitment_no_commit(
        db_session,
        TEST_USER_ID,
        CommitmentCreate(
            name="Internet", amount=Decimal("30000"), due_date=TODAY, category="servicios"
        ),
    )
    db_session.commit()
    commitment_id = commitment.id

    def boom(*args, **kwargs):
        raise SQLAlchemyError("falla simulada")

    monkeypatch.setattr(transaction_service, "create_transaction_no_commit", boom)

    try:
        commitment_service.update_commitment(
            db_session,
            TEST_USER_ID,
            commitment_id,
            CommitmentUpdate(status=CommitmentStatus.PAID),
        )
    except SQLAlchemyError:
        pass
    else:
        raise AssertionError("La falla simulada debió propagarse")

    stored = db_session.get(Commitment, commitment_id)
    assert stored.status is CommitmentStatus.PENDING
    assert db_session.query(Transaction).count() == 0


def test_dos_pagos_concurrentes_no_crean_dos_movimientos() -> None:
    user_id = UUID("33333333-3333-4333-8333-333333333333")
    with Session(engine) as setup:
        existing = setup.get(UserProfile, user_id)
        if existing is not None:
            setup.delete(existing)
            setup.commit()
        setup.add(UserProfile(id=user_id, name="Concurrente", current_balance=Decimal("620000.00")))
        setup.flush()
        commitment = Commitment(
            user_id=user_id,
            name="Internet",
            amount=Decimal("30000.00"),
            due_date=TODAY,
            category="servicios",
            status=CommitmentStatus.PENDING,
        )
        setup.add(commitment)
        setup.commit()
        commitment_id = commitment.id

    def pay() -> str:
        with Session(engine) as session:
            paid = commitment_service.update_commitment(
                session,
                user_id,
                commitment_id,
                CommitmentUpdate(status=CommitmentStatus.PAID),
            )
            return paid.status.value

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _: pay(), range(2))) == ["paid", "paid"]

        with Session(engine) as verify:
            txs = (
                verify.execute(
                    select(Transaction).where(Transaction.commitment_id == commitment_id)
                )
                .scalars()
                .all()
            )
            profile = verify.get(UserProfile, user_id)
            assert len(txs) == 1
            assert profile.current_balance == Decimal("590000.00")
    finally:
        with Session(engine) as cleanup:
            profile = cleanup.get(UserProfile, user_id)
            if profile is not None:
                cleanup.delete(profile)
                cleanup.commit()


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
