"""Aislamiento de la capa de IA entre cuentas.

Es el contrato central de esta fase. Hasta ahora los endpoints de IA eran públicos y todo
—borradores, conversaciones, checkpoints, RAG— colgaba de un usuario fijo. Ahora el dueño
sale del JWT verificado y nada del cliente ni del modelo puede cambiarlo.

Se prueban cuatro cosas, que se rompen por motivos distintos:

1. Sin sesión no se accede a nada, y no se gasta una llamada al modelo.
2. Un borrador ajeno no se puede consultar, confirmar ni rechazar.
3. Una conversación ajena no se puede leer ni reanudar.
4. El copiloto y el RAG responden solo con los datos de quien pregunta.

El gateway es siempre el mock: estos tests no llaman a ningún proveedor real.
"""

import uuid
from collections.abc import Callable, Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockAIProvider
from app.main import app
from app.models.ai_draft import AIDraft
from app.models.transaction import Transaction
from app.services.draft_store import InMemoryDraftStore, get_draft_store
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

# Los endpoints de IA que esta fase pasó a exigir sesión.
AI_ENDPOINTS = [
    ("POST", f"{API}/ai/transactions/parse", {"text": "Gasté 25 lucas en nafta"}),
    (
        "POST",
        f"{API}/ai/transactions/11111111-1111-4111-8111-111111111111/confirm",
        {"confirmed": True},
    ),
    ("POST", f"{API}/ai/transactions/11111111-1111-4111-8111-111111111111/reject", None),
    ("POST", f"{API}/ai/chat", {"message": "¿Cuánto puedo gastar hoy?"}),
    ("GET", f"{API}/ai/conversations/11111111-1111-4111-8111-111111111111", None),
    (
        "POST",
        f"{API}/ai/conversations/11111111-1111-4111-8111-111111111111/approve",
        {"action_id": "11111111-1111-4111-8111-111111111111"},
    ),
    (
        "POST",
        f"{API}/ai/conversations/11111111-1111-4111-8111-111111111111/reject",
        {"action_id": "11111111-1111-4111-8111-111111111111"},
    ),
]


class CountingProvider(MockAIProvider):
    """Proveedor mock que cuenta llamadas, para comprobar que un 401 no gasta ninguna."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def parse_transaction(self, *args, **kwargs):
        self.calls += 1
        return super().parse_transaction(*args, **kwargs)


@pytest.fixture
def ai_store() -> InMemoryDraftStore:
    """Un único store en memoria compartido por los clientes del test.

    Compartirlo es lo que hace significativo el test: los dos usuarios miran el MISMO
    almacén, así que el aislamiento tiene que salir del filtro por dueño y no de que cada
    uno tenga su propia estructura de datos.
    """
    return InMemoryDraftStore()


@pytest.fixture
def ai_client_for(
    client_for: Callable[..., TestClient], ai_store: InMemoryDraftStore
) -> Generator[Callable[..., TestClient], None, None]:
    """Fábrica de clientes autenticados con gateway mock y draft store compartido."""
    app.dependency_overrides[get_draft_store] = lambda: ai_store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        yield client_for
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


def _profile(client: TestClient, **overrides: object) -> dict:
    response = client.put(f"{API}/profile", json=default_profile_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _parse(client: TestClient, text: str = "Gasté 25 lucas ayer en nafta con débito") -> dict:
    response = client.post(f"{API}/ai/transactions/parse", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


# ---------- 1. Sin sesión no se accede a nada ----------


def test_todos_los_endpoints_de_ia_exigen_sesion(anonymous_client: TestClient) -> None:
    for method, path, body in AI_ENDPOINTS:
        response = anonymous_client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} devolvió {response.status_code}"


def test_sin_sesion_no_se_llama_al_modelo(anonymous_client: TestClient) -> None:
    """El 401 corta antes del gateway: una petición sin token no gasta una llamada paga."""
    provider = CountingProvider()
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(provider)
    try:
        response = anonymous_client.post(
            f"{API}/ai/transactions/parse", json={"text": "Gasté 25 lucas en nafta"}
        )
    finally:
        app.dependency_overrides.pop(get_ai_gateway, None)

    assert response.status_code == 401
    assert provider.calls == 0


def test_sin_sesion_no_se_crea_ningun_borrador(
    anonymous_client: TestClient, db_session: Session
) -> None:
    store = InMemoryDraftStore()
    antes = len(db_session.execute(select(AIDraft)).scalars().all())
    app.dependency_overrides[get_draft_store] = lambda: store
    try:
        anonymous_client.post(f"{API}/ai/transactions/parse", json={"text": "Gasté 25 lucas"})
    finally:
        app.dependency_overrides.pop(get_draft_store, None)

    assert store._drafts == {}
    # Se cuenta el delta: `ai_drafts` no cuelga del perfil, así que la base de desarrollo
    # puede tener filas de corridas anteriores y exigir la tabla vacía sería frágil.
    assert len(db_session.execute(select(AIDraft)).scalars().all()) == antes


# ---------- 2. Borradores ----------


def test_el_borrador_queda_a_nombre_de_quien_lo_pidio(
    ai_client_for: Callable[..., TestClient], ai_store: InMemoryDraftStore
) -> None:
    _profile(ai_client_for(OTHER_USER_ID, OTHER_USER_EMAIL))

    body = _parse(ai_client_for(OTHER_USER_ID, OTHER_USER_EMAIL))

    draft = ai_store._drafts[uuid.UUID(body["draft_id"])]
    assert draft.user_id == OTHER_USER_ID


def test_un_borrador_ajeno_no_se_puede_confirmar(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID, TEST_USER_EMAIL), current_balance="500000.00")
    _profile(ai_client_for(OTHER_USER_ID, OTHER_USER_EMAIL), current_balance="500000.00")
    ajeno = _parse(ai_client_for(TEST_USER_ID))

    intruso = ai_client_for(OTHER_USER_ID)
    response = intruso.post(
        f"{API}/ai/transactions/{ajeno['draft_id']}/confirm", json={"confirmed": True}
    )

    assert response.status_code == 404
    # Ni un movimiento ni un peso se movieron en ninguna de las dos cuentas.
    assert intruso.get(f"{API}/transactions").json() == []
    duenio = ai_client_for(TEST_USER_ID)
    assert duenio.get(f"{API}/transactions").json() == []
    assert Decimal(duenio.get(f"{API}/profile").json()["current_balance"]) == Decimal("500000.00")


def test_un_borrador_ajeno_no_se_puede_rechazar(
    ai_client_for: Callable[..., TestClient], ai_store: InMemoryDraftStore
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    ajeno = _parse(ai_client_for(TEST_USER_ID))

    response = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/transactions/{ajeno['draft_id']}/reject"
    )

    assert response.status_code == 404
    # Y sigue disponible para su dueño, que es quien decide.
    confirmar = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/transactions/{ajeno['draft_id']}/confirm", json={"confirmed": True}
    )
    assert confirmar.status_code == 201


def test_un_borrador_ajeno_responde_igual_que_uno_inexistente(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    ajeno = _parse(ai_client_for(TEST_USER_ID))
    inexistente = "99999999-9999-4999-8999-999999999999"

    intruso = ai_client_for(OTHER_USER_ID)
    uno = intruso.post(f"{API}/ai/transactions/{ajeno['draft_id']}/reject")
    otro = intruso.post(f"{API}/ai/transactions/{inexistente}/reject")

    assert uno.status_code == otro.status_code == 404
    assert uno.json() == otro.json()


def test_confirmar_el_propio_borrador_afecta_solo_al_propio_saldo(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID), current_balance="500000.00")
    _profile(ai_client_for(OTHER_USER_ID), current_balance="500000.00")
    propio = _parse(ai_client_for(OTHER_USER_ID))

    response = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/transactions/{propio['draft_id']}/confirm", json={"confirmed": True}
    )

    assert response.status_code == 201
    movido = ai_client_for(OTHER_USER_ID).get(f"{API}/profile").json()
    intacto = ai_client_for(TEST_USER_ID).get(f"{API}/profile").json()
    assert Decimal(movido["current_balance"]) < Decimal("500000.00")
    assert Decimal(intacto["current_balance"]) == Decimal("500000.00")
    assert len(ai_client_for(TEST_USER_ID).get(f"{API}/transactions").json()) == 0


def test_la_doble_confirmacion_sigue_creando_un_solo_movimiento(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """La idempotencia del human-in-the-loop no se tocó al agregar el dueño."""
    _profile(ai_client_for(TEST_USER_ID))
    propio = _parse(ai_client_for(TEST_USER_ID))

    duenio = ai_client_for(TEST_USER_ID)
    primera = duenio.post(
        f"{API}/ai/transactions/{propio['draft_id']}/confirm", json={"confirmed": True}
    )
    segunda = duenio.post(
        f"{API}/ai/transactions/{propio['draft_id']}/confirm", json={"confirmed": True}
    )

    assert primera.status_code == 201
    assert segunda.status_code == 409
    assert len(duenio.get(f"{API}/transactions").json()) == 1


# ---------- 3. Conversaciones y checkpoints ----------


def test_no_se_puede_leer_la_conversacion_de_otra_cuenta(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """El conversation_id viaja por la URL: no puede alcanzar para leer el hilo ajeno."""
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    propia = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Cuánto puedo gastar hoy?"}
    )
    assert propia.status_code == 200
    conversation_id = propia.json()["conversation_id"]

    del_duenio = ai_client_for(TEST_USER_ID).get(f"{API}/ai/conversations/{conversation_id}")
    del_intruso = ai_client_for(OTHER_USER_ID).get(f"{API}/ai/conversations/{conversation_id}")

    assert len(del_duenio.json()["messages"]) > 0
    # Para la otra cuenta ese id resuelve un hilo vacío, no la conversación ajena.
    assert del_intruso.status_code == 200
    assert del_intruso.json()["messages"] == []


def test_no_se_puede_aprobar_la_accion_pendiente_de_otra_cuenta(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID), current_balance="500000.00")
    _profile(ai_client_for(OTHER_USER_ID), current_balance="500000.00")
    pendiente = (
        ai_client_for(TEST_USER_ID)
        .post(f"{API}/ai/chat", json={"message": "Gasté 25 lucas ayer en nafta con débito"})
        .json()
    )
    assert pendiente["requires_approval"] is True
    conversation_id = pendiente["conversation_id"]
    action_id = pendiente["pending_action"]["action_id"]

    intruso = ai_client_for(OTHER_USER_ID)
    aprobar = intruso.post(
        f"{API}/ai/conversations/{conversation_id}/approve", json={"action_id": action_id}
    )

    assert aprobar.status_code == 404
    duenio = ai_client_for(TEST_USER_ID)
    assert duenio.get(f"{API}/transactions").json() == []
    assert Decimal(duenio.get(f"{API}/profile").json()["current_balance"]) == Decimal("500000.00")


def test_no_se_puede_rechazar_la_accion_pendiente_de_otra_cuenta(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    pendiente = (
        ai_client_for(TEST_USER_ID)
        .post(f"{API}/ai/chat", json={"message": "Gasté 25 lucas ayer en nafta con débito"})
        .json()
    )

    rechazar = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/conversations/{pendiente['conversation_id']}/reject",
        json={"action_id": pendiente["pending_action"]["action_id"]},
    )

    assert rechazar.status_code == 404
    # La acción sigue pendiente para su dueño, que es quien decide.
    aprobar = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/conversations/{pendiente['conversation_id']}/approve",
        json={"action_id": pendiente["pending_action"]["action_id"]},
    )
    assert aprobar.status_code == 200


def test_dos_cuentas_no_comparten_el_hilo_de_conversacion(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """Mismo conversation_id, dos hilos distintos: la memoria multi-turn no se cruza."""
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    conversation_id = "44444444-4444-4444-8444-444444444444"

    ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/chat",
        json={"message": "¿Cuánto puedo gastar hoy?", "conversation_id": conversation_id},
    )
    ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat",
        json={"message": "¿Qué pagos tengo antes de cobrar?", "conversation_id": conversation_id},
    )

    de_a = ai_client_for(TEST_USER_ID).get(f"{API}/ai/conversations/{conversation_id}").json()
    de_b = ai_client_for(OTHER_USER_ID).get(f"{API}/ai/conversations/{conversation_id}").json()
    textos_a = [m["content"] for m in de_a["messages"]]
    textos_b = [m["content"] for m in de_b["messages"]]

    assert "¿Cuánto puedo gastar hoy?" in textos_a
    assert "¿Cuánto puedo gastar hoy?" not in textos_b
    assert "¿Qué pagos tengo antes de cobrar?" in textos_b
    assert "¿Qué pagos tengo antes de cobrar?" not in textos_a


# ---------- 4. El copiloto y el RAG solo ven lo propio ----------


def test_el_copiloto_responde_con_los_compromisos_de_quien_pregunta(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    ai_client_for(TEST_USER_ID).post(
        f"{API}/commitments",
        json={
            "name": "Gimnasio",
            "amount": "300000",
            "due_date": "2026-07-30",
            "category": "vivienda",
        },
    )

    de_a = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Qué pagos tengo antes de cobrar?"}
    )
    de_b = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Qué pagos tengo antes de cobrar?"}
    )

    # La capa de presentación normaliza el texto, así que se compara sin mayúsculas.
    assert "gimnasio" in de_a.json()["answer"].lower()
    assert "gimnasio" not in de_b.json()["answer"].lower()


def test_la_busqueda_del_rag_no_devuelve_movimientos_ajenos(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """El RAG ya filtraba por user_id; lo que cambió es de quién es ese user_id."""
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    ai_client_for(TEST_USER_ID).post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "12000.00",
            "category": "transporte",
            "description": "carga de nafta",
            "occurred_on": "2026-07-20",
        },
    )

    de_a = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/chat", json={"message": "Buscar gastos de nafta"}
    )
    de_b = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat", json={"message": "Buscar gastos de nafta"}
    )

    assert len(de_a.json()["evidence"]) >= 1
    assert de_b.json()["evidence"] == []


def test_el_saldo_que_reporta_el_copiloto_es_el_de_cada_usuario(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID), current_balance="111000.00")
    _profile(ai_client_for(OTHER_USER_ID), current_balance="999000.00")

    de_a = ai_client_for(TEST_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Cuánto puedo gastar hoy?"}
    )
    de_b = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Cuánto puedo gastar hoy?"}
    )

    assert de_a.json()["answer"] != de_b.json()["answer"]


# ---------- El modelo no elige el usuario ----------


def test_ninguna_tool_declara_user_id_en_su_schema() -> None:
    """El dueño viaja por el contexto de ejecución, no por los argumentos del modelo.

    Si alguna tool lo declarara, el LLM podría proponerlo —y una inyección de prompt
    tendría por dónde entrar.
    """
    from app.ai.agent.tools import TOOLS

    for name, tool in TOOLS.items():
        campos = set(tool.args_model.model_fields)
        assert "user_id" not in campos, f"la tool {name} expone user_id al modelo"
        assert "profile_id" not in campos, f"la tool {name} expone profile_id al modelo"


def test_un_user_id_en_el_cuerpo_del_chat_se_rechaza(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """Los schemas de entrada son `extra="forbid"`: el campo ni siquiera entra al servicio."""
    _profile(ai_client_for(OTHER_USER_ID))

    response = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat",
        json={"message": "¿Cuánto puedo gastar hoy?", "user_id": str(TEST_USER_ID)},
    )

    assert response.status_code == 422


def test_un_user_id_en_el_parse_se_rechaza(
    ai_client_for: Callable[..., TestClient], ai_store: InMemoryDraftStore
) -> None:
    _profile(ai_client_for(OTHER_USER_ID))

    response = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/transactions/parse",
        json={"text": "Gasté 25 lucas ayer en nafta con débito", "user_id": str(TEST_USER_ID)},
    )

    assert response.status_code == 422
    assert ai_store._drafts == {}


# ---------- Lo que la IA escribe queda a nombre de quien pidió ----------


def test_la_transaccion_creada_por_ia_es_del_usuario_autenticado(
    ai_client_for: Callable[..., TestClient], db_session: Session
) -> None:
    """No alcanza con que el saldo del otro no se mueva: la fila tiene que llevar su dueño.

    Se mira la tabla directamente porque es la única forma de ver el `user_id` que quedó
    escrito; por la API cada quien ve solo lo suyo y un error de atribución podría pasar
    desapercibido.
    """
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    propio = _parse(ai_client_for(OTHER_USER_ID))

    creada = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/transactions/{propio['draft_id']}/confirm", json={"confirmed": True}
    )

    assert creada.status_code == 201
    transaction_id = uuid.UUID(creada.json()["transaction"]["id"])
    duenio = db_session.execute(
        select(Transaction.user_id).where(Transaction.id == transaction_id)
    ).scalar_one()
    assert duenio == OTHER_USER_ID


def test_la_transaccion_que_aprueba_el_copiloto_es_del_usuario_autenticado(
    ai_client_for: Callable[..., TestClient], db_session: Session
) -> None:
    """El otro camino de escritura: la acción pendiente del grafo, aprobada por su dueño."""
    _profile(ai_client_for(TEST_USER_ID))
    _profile(ai_client_for(OTHER_USER_ID))
    pendiente = (
        ai_client_for(OTHER_USER_ID)
        .post(f"{API}/ai/chat", json={"message": "Gasté 25 lucas ayer en nafta con débito"})
        .json()
    )
    assert pendiente["requires_approval"] is True

    aprobada = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/conversations/{pendiente['conversation_id']}/approve",
        json={"action_id": pendiente["pending_action"]["action_id"]},
    )

    assert aprobada.status_code == 200
    duenios = db_session.execute(select(Transaction.user_id)).scalars().all()
    assert duenios == [OTHER_USER_ID]


def test_la_consulta_de_una_cuenta_no_mira_los_movimientos_de_la_otra(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """Dos cuentas con gastos distintos preguntan lo mismo y no se cruzan las respuestas."""
    _profile(ai_client_for(TEST_USER_ID), current_balance="500000.00")
    _profile(ai_client_for(OTHER_USER_ID), current_balance="500000.00")

    ai_client_for(TEST_USER_ID).post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "123456.00",
            "category": "supermercado",
            "description": "compra enorme de una cuenta",
            "occurred_on": "2026-07-30",
        },
    )

    respuesta = ai_client_for(OTHER_USER_ID).post(
        f"{API}/ai/chat", json={"message": "¿Cuánto gasté en supermercado?"}
    )

    assert respuesta.status_code == 200
    body = respuesta.json()
    assert "123456" not in body["answer"].replace(".", "").replace(",", "")
    # Y ninguna evidencia citada puede venir de la otra cuenta.
    assert all("compra enorme" not in e["title"] for e in body["evidence"])
