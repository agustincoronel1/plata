"""Aislamiento entre cuentas: cada usuario ve y toca solo lo suyo.

Es el contrato central de esta fase. Antes, todos los endpoints financieros operaban sobre
un perfil fijo (`DEMO_USER_ID`); ahora el UUID sale del JWT verificado y nada del cliente
puede cambiarlo.

Se prueban tres cosas distintas, que se rompen por motivos distintos:

1. Sin sesión no se accede a nada (401).
2. Con sesión, cada usuario ve exclusivamente sus datos.
3. Con sesión, un id ajeno responde 404: no se puede leer, editar ni borrar por id, ni
   averiguar si ese recurso existe.

Los dos usuarios comparten las mismas tablas, así que estos tests también confirman que el
filtrado es por consulta y no por "la base está vacía".
"""

from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import (
    API,
    OTHER_USER_EMAIL,
    OTHER_USER_ID,
    TEST_USER_EMAIL,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

pytestmark = requires_postgres

# Todos los endpoints financieros que esta fase pasó a multiusuario.
FINANCIAL_ENDPOINTS = [
    ("GET", f"{API}/profile", None),
    ("PUT", f"{API}/profile", default_profile_payload()),
    ("GET", f"{API}/transactions", None),
    ("POST", f"{API}/transactions", {"type": "expense", "amount": "1000", "category": "comida"}),
    ("PATCH", f"{API}/transactions/11111111-1111-4111-8111-111111111111", {"amount": "2000"}),
    ("DELETE", f"{API}/transactions/11111111-1111-4111-8111-111111111111", None),
    ("GET", f"{API}/commitments", None),
    ("POST", f"{API}/commitments", {"name": "Luz", "amount": "5000", "due_date": "2026-08-10"}),
    ("PATCH", f"{API}/commitments/11111111-1111-4111-8111-111111111111", {"status": "paid"}),
    ("DELETE", f"{API}/commitments/11111111-1111-4111-8111-111111111111", None),
    ("GET", f"{API}/dashboard/summary", None),
    ("GET", f"{API}/simulations", None),
    (
        "POST",
        f"{API}/simulations/purchase",
        {
            "purchase_name": "Notebook",
            "total_amount": "600000",
            "installments": 6,
            "first_installment_date": "2026-08-15",
        },
    ),
]


def _profile_for(client: TestClient, **overrides: object) -> dict:
    response = client.put(f"{API}/profile", json=default_profile_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _transaction(client: TestClient, **overrides: object) -> dict:
    payload = {
        "type": "expense",
        "amount": "10000.00",
        "category": "comida",
        "description": "almuerzo",
        "occurred_on": "2026-07-20",
    }
    payload.update(overrides)
    response = client.post(f"{API}/transactions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _commitment(client: TestClient, **overrides: object) -> dict:
    payload = {
        "name": "Alquiler",
        "amount": "300000.00",
        "due_date": "2026-08-05",
        "category": "vivienda",
    }
    payload.update(overrides)
    response = client.post(f"{API}/commitments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _simulation(client: TestClient, **overrides: object) -> dict:
    payload = {
        "purchase_name": "Notebook",
        "total_amount": "600000.00",
        "installments": 6,
        "first_installment_date": "2026-08-15",
    }
    payload.update(overrides)
    response = client.post(f"{API}/simulations/purchase", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- 1. Sin sesión no se accede a nada ----------


def test_todos_los_endpoints_financieros_exigen_sesion(anonymous_client: TestClient) -> None:
    """Ninguno de estos endpoints era privado antes de esta fase. Ahora todos lo son."""
    for method, path, body in FINANCIAL_ENDPOINTS:
        response = anonymous_client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} devolvió {response.status_code}"


def test_sin_sesion_no_se_filtra_informacion_en_el_401(anonymous_client: TestClient) -> None:
    """El 401 no dice si el perfil existe ni cuánta plata hay: solo que falta sesión."""
    response = anonymous_client.get(f"{API}/dashboard/summary")

    assert response.status_code == 401
    assert response.json() == {"detail": "Tu sesión no es válida o expiró. Iniciá sesión de nuevo."}


# ---------- 2. Cada usuario ve solo lo suyo ----------


def test_una_cuenta_nueva_no_tiene_perfil(client_for: Callable[..., TestClient]) -> None:
    """El 404 es lo que dispara el onboarding: una cuenta nueva no hereda datos de nadie."""
    _profile_for(client_for(TEST_USER_ID, TEST_USER_EMAIL))

    response = client_for(OTHER_USER_ID, OTHER_USER_EMAIL).get(f"{API}/profile")

    assert response.status_code == 404


def test_el_perfil_creado_se_identifica_con_el_uuid_de_la_sesion(
    client_for: Callable[..., TestClient],
) -> None:
    """El id del perfil ES el `sub` del token: el cliente no lo elige ni lo manda."""
    creado = _profile_for(client_for(OTHER_USER_ID, OTHER_USER_EMAIL))

    assert creado["id"] == str(OTHER_USER_ID)


def test_cada_usuario_ve_su_propio_perfil(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID), name="Persona A", current_balance="100000.00")
    _profile_for(client_for(OTHER_USER_ID), name="Persona B", current_balance="777000.00")

    a = client_for(TEST_USER_ID).get(f"{API}/profile").json()
    b = client_for(OTHER_USER_ID).get(f"{API}/profile").json()

    assert a["name"] == "Persona A"
    assert a["current_balance"] == "100000.00"
    assert b["name"] == "Persona B"
    assert b["current_balance"] == "777000.00"


def test_los_movimientos_no_se_mezclan(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    _transaction(client_for(TEST_USER_ID), description="gasto de A")
    _transaction(client_for(OTHER_USER_ID), description="gasto de B")

    de_a = client_for(TEST_USER_ID).get(f"{API}/transactions").json()
    de_b = client_for(OTHER_USER_ID).get(f"{API}/transactions").json()

    assert [t["description"] for t in de_a] == ["gasto de A"]
    assert [t["description"] for t in de_b] == ["gasto de B"]


def test_los_compromisos_no_se_mezclan(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    _commitment(client_for(TEST_USER_ID), name="Alquiler de A")
    _commitment(client_for(OTHER_USER_ID), name="Alquiler de B")

    de_a = client_for(TEST_USER_ID).get(f"{API}/commitments").json()
    de_b = client_for(OTHER_USER_ID).get(f"{API}/commitments").json()

    assert [c["name"] for c in de_a] == ["Alquiler de A"]
    assert [c["name"] for c in de_b] == ["Alquiler de B"]


def test_las_simulaciones_no_se_mezclan(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    _simulation(client_for(TEST_USER_ID), purchase_name="Notebook de A")
    _simulation(client_for(OTHER_USER_ID), purchase_name="Notebook de B")

    de_a = client_for(TEST_USER_ID).get(f"{API}/simulations").json()
    de_b = client_for(OTHER_USER_ID).get(f"{API}/simulations").json()

    assert [s["purchase_name"] for s in de_a] == ["Notebook de A"]
    assert [s["purchase_name"] for s in de_b] == ["Notebook de B"]


def test_el_saldo_de_uno_no_se_mueve_por_el_gasto_del_otro(
    client_for: Callable[..., TestClient],
) -> None:
    """La política de saldo no cambió; lo que cambió es a qué perfil se aplica."""
    _profile_for(client_for(TEST_USER_ID), current_balance="500000.00")
    _profile_for(client_for(OTHER_USER_ID), current_balance="500000.00")

    _transaction(client_for(TEST_USER_ID), amount="30000.00")

    a = client_for(TEST_USER_ID).get(f"{API}/profile").json()
    b = client_for(OTHER_USER_ID).get(f"{API}/profile").json()

    assert Decimal(a["current_balance"]) == Decimal("470000.00")
    assert Decimal(b["current_balance"]) == Decimal("500000.00")


def test_el_dashboard_calcula_con_los_datos_de_cada_usuario(
    client_for: Callable[..., TestClient],
) -> None:
    """El motor financiero es el mismo; los compromisos que descuenta son los propios."""
    _profile_for(client_for(TEST_USER_ID), current_balance="500000.00")
    _profile_for(client_for(OTHER_USER_ID), current_balance="500000.00")
    # Vence antes del próximo ingreso (2026-08-01), así entra en el horizonte que descuenta
    # el motor financiero. La regla es la de siempre; acá se comprueba de quién es el dato.
    _commitment(client_for(TEST_USER_ID), amount="200000.00", due_date="2026-07-31")

    a = client_for(TEST_USER_ID).get(f"{API}/dashboard/summary").json()
    b = client_for(OTHER_USER_ID).get(f"{API}/dashboard/summary").json()

    assert Decimal(a["pending_commitments_amount"]) == Decimal("200000.00")
    assert Decimal(b["pending_commitments_amount"]) == Decimal("0.00")
    assert a["available_real"] != b["available_real"]


# ---------- 3. Un id ajeno responde 404 ----------


def test_no_se_puede_leer_un_movimiento_ajeno_editandolo(
    client_for: Callable[..., TestClient],
) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    ajena = _transaction(client_for(TEST_USER_ID))

    response = client_for(OTHER_USER_ID).patch(
        f"{API}/transactions/{ajena['id']}", json={"amount": "1.00"}
    )

    assert response.status_code == 404
    # Y el movimiento quedó intacto para su dueño.
    de_a = client_for(TEST_USER_ID).get(f"{API}/transactions").json()
    assert Decimal(de_a[0]["amount"]) == Decimal("10000.00")


def test_no_se_puede_borrar_un_movimiento_ajeno(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    ajena = _transaction(client_for(TEST_USER_ID))

    response = client_for(OTHER_USER_ID).delete(f"{API}/transactions/{ajena['id']}")

    assert response.status_code == 404
    assert len(client_for(TEST_USER_ID).get(f"{API}/transactions").json()) == 1


def test_borrar_un_movimiento_ajeno_no_toca_ningun_saldo(
    client_for: Callable[..., TestClient],
) -> None:
    """Un 404 tiene que ser inerte: ni el saldo del dueño ni el del intruso se mueven."""
    _profile_for(client_for(TEST_USER_ID), current_balance="500000.00")
    _profile_for(client_for(OTHER_USER_ID), current_balance="500000.00")
    ajena = _transaction(client_for(TEST_USER_ID), amount="30000.00")

    assert client_for(OTHER_USER_ID).delete(f"{API}/transactions/{ajena['id']}").status_code == 404

    a = client_for(TEST_USER_ID).get(f"{API}/profile").json()
    b = client_for(OTHER_USER_ID).get(f"{API}/profile").json()
    assert Decimal(a["current_balance"]) == Decimal("470000.00")
    assert Decimal(b["current_balance"]) == Decimal("500000.00")


def test_no_se_puede_tocar_un_compromiso_ajeno(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    ajeno = _commitment(client_for(TEST_USER_ID))

    intruso = client_for(OTHER_USER_ID)
    editar = intruso.patch(f"{API}/commitments/{ajeno['id']}", json={"status": "paid"})
    assert editar.status_code == 404
    assert intruso.delete(f"{API}/commitments/{ajeno['id']}").status_code == 404

    propio = client_for(TEST_USER_ID).get(f"{API}/commitments").json()
    assert propio[0]["status"] == "pending"


def test_un_recurso_ajeno_responde_igual_que_uno_inexistente(
    client_for: Callable[..., TestClient],
) -> None:
    """404 en ambos casos: nadie puede usar el código de estado para descubrir qué existe."""
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    ajena = _transaction(client_for(TEST_USER_ID))
    inexistente = "99999999-9999-4999-8999-999999999999"

    intruso = client_for(OTHER_USER_ID)
    ajena_res = intruso.delete(f"{API}/transactions/{ajena['id']}")
    inexistente_res = intruso.delete(f"{API}/transactions/{inexistente}")

    assert ajena_res.status_code == inexistente_res.status_code == 404
    assert ajena_res.json() == inexistente_res.json()


# ---------- El cliente no elige de quién son los datos ----------


def test_un_user_id_en_el_cuerpo_se_ignora(client_for: Callable[..., TestClient]) -> None:
    """Mandar `user_id` no cambia nada: el schema lo descarta y manda el token."""
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))

    response = client_for(OTHER_USER_ID).post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "10000.00",
            "category": "comida",
            "occurred_on": "2026-07-20",
            "user_id": str(TEST_USER_ID),
        },
    )

    assert response.status_code == 201
    # El movimiento quedó en la cuenta de quien lo pidió, no en la que dice el cuerpo.
    assert client_for(TEST_USER_ID).get(f"{API}/transactions").json() == []
    assert len(client_for(OTHER_USER_ID).get(f"{API}/transactions").json()) == 1


def test_un_user_id_en_la_query_se_ignora(client_for: Callable[..., TestClient]) -> None:
    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    _transaction(client_for(TEST_USER_ID), description="gasto de A")

    ajenos = client_for(OTHER_USER_ID).get(f"{API}/transactions?user_id={TEST_USER_ID}").json()

    assert ajenos == []


def test_borrar_el_perfil_se_lleva_solo_los_datos_propios(
    client_for: Callable[..., TestClient], db_session: Session
) -> None:
    """El ON DELETE CASCADE está acotado por `user_id`: no arrastra datos de terceros."""
    from app.models import Transaction, UserProfile

    _profile_for(client_for(TEST_USER_ID))
    _profile_for(client_for(OTHER_USER_ID))
    _transaction(client_for(TEST_USER_ID))
    _transaction(client_for(OTHER_USER_ID))

    db_session.delete(db_session.get(UserProfile, OTHER_USER_ID))
    db_session.flush()

    restantes = db_session.query(Transaction).all()
    assert [t.user_id for t in restantes] == [TEST_USER_ID]
