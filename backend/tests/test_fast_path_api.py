"""Tests de integración del fast path sobre /api/v1/ai/chat.

Lo que se prueba acá no es solo que los números salgan bien: es que el atajo cumpla su
promesa. Una consulta simple tiene que responder **sin** llamar al proveedor, sin generar
embeddings, sin correr el grafo y sin gastar una de las 10 consultas del día. Cada una de
esas cuatro cosas tiene su propio test y falla por su propio motivo.

El resto verifica lo de siempre en Plata: que los datos sean los del usuario autenticado y
de nadie más, y que lo que el fast path no reconoce siga exactamente como estaba.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.ai.agent.schemas import AgentIntent
from app.ai.fast_path import Period, match_fast_path
from app.ai.gateway import get_ai_gateway
from app.ai.rag.embeddings import MockEmbeddingProvider
from app.api.usage_headers import LIMIT_HEADER, REMAINING_HEADER
from app.core.timezone import app_today
from app.main import app
from app.services import ai_chat_service, fast_path_service
from app.services.draft_store import InMemoryDraftStore, get_draft_store
from app.services.fast_path_service import period_bounds
from tests.conftest import (
    API,
    OTHER_USER_EMAIL,
    OTHER_USER_ID,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

pytestmark = requires_postgres

CHAT = f"{API}/ai/chat"
USAGE = f"{API}/ai/usage"


class ExplodingProvider:
    """Proveedor que estalla ante cualquier uso. Si el fast path lo toca, el test falla."""

    def parse_transaction(self, **kwargs: object) -> object:
        raise AssertionError("El fast path llamó al proveedor de IA")


class ExplodingBrain:
    """Cerebro que estalla al clasificar o redactar.

    `classify` es el primer nodo del grafo, así que si esto se dispara significa que el
    turno entró a LangGraph en lugar de resolverse por el atajo.
    """

    def classify(self, message: str, history: list[dict]) -> dict:
        raise AssertionError("El fast path entró al grafo de LangGraph")

    def answer(self, intent: AgentIntent, context: dict) -> str:
        raise AssertionError("El fast path le pidió texto al modelo")


@pytest.fixture
def fast_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    """Cliente donde cualquier uso del modelo revienta.

    Es la fixture central del archivo: el proveedor y el cerebro quedan minados. Un test
    que responde 200 con esto puesto demuestra, por construcción, que el turno no pasó por
    el modelo ni por el grafo (el primer nodo del grafo es justamente `classify`).

    Los embeddings no se minan acá: dar de alta un movimiento lo indexa para el RAG, y ese
    indexado es best-effort (se traga la excepción y loguea), así que una mina no
    distinguiría el alta del turno de chat. Eso se prueba contando, en su propio test.
    """
    monkeypatch.setattr(
        ai_chat_service, "build_brain", lambda settings: ExplodingBrain(), raising=True
    )
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    app.dependency_overrides[get_ai_gateway] = ExplodingProvider
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


def _ask(client: TestClient, message: str) -> dict:
    response = client.post(CHAT, json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _expense(client: TestClient, amount: str, category: str, **extra: object) -> None:
    payload = {"type": "expense", "amount": amount, "category": category, **extra}
    response = client.post(f"{API}/transactions", json=payload)
    assert response.status_code == 201, response.text


def _income(client: TestClient, amount: str, **extra: object) -> None:
    payload = {"type": "income", "amount": amount, "category": "sueldo", **extra}
    response = client.post(f"{API}/transactions", json=payload)
    assert response.status_code == 201, response.text


# ---------- Montos, categorías y rangos ----------


def test_expense_total_del_mes(fast_client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    _expense(fast_client, "60000.00", "comida")
    _expense(fast_client, "25400.00", "transporte")

    body = _ask(fast_client, "¿cuánto gasté este mes?")

    assert body["source"] == "fast_path"
    assert "$85.400" in body["answer"]
    assert "este mes" in body["answer"]
    # Sin tools, sin evidencia y sin aprobación: el atajo no usa nada de eso.
    assert body["tools_used"] == []
    assert body["evidence"] == []
    assert body["requires_approval"] is False


def test_expense_por_categoria_solo_suma_esa_categoria(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _expense(fast_client, "42000.00", "servicios")
    _expense(fast_client, "99000.00", "comida")

    body = _ask(fast_client, "¿cuánto gasté en servicios este mes?")

    assert "$42.000" in body["answer"]
    assert "servicios" in body["answer"]
    assert "$99.000" not in body["answer"]


def test_expense_de_hoy_no_cuenta_los_dias_anteriores(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    today = app_today()
    _expense(fast_client, "10000.00", "comida", occurred_on=today.isoformat())
    _expense(fast_client, "77000.00", "comida", occurred_on=(today - timedelta(days=5)).isoformat())

    body = _ask(fast_client, "¿cuánto gasté hoy?")

    assert "$10.000" in body["answer"]
    assert "hoy" in body["answer"]
    assert "$87.000" not in body["answer"]


def test_income_total_del_mes(fast_client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    _income(fast_client, "1200000.00")
    _expense(fast_client, "50000.00", "comida")

    body = _ask(fast_client, "¿cuánto ingresé este mes?")

    # El gasto no se mezcla con los ingresos.
    assert "$1.200.000" in body["answer"]
    assert "$50.000" not in body["answer"]


def test_saldo_actual_sale_del_perfil(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _ask(fast_client, "¿cuál es mi saldo?")
    assert "$620.000" in body["answer"]


def test_disponible_usa_la_formula_del_motor(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Mismo número que el dashboard: el fast path no tiene su propia cuenta."""
    make_profile()
    fast_client.post(
        f"{API}/commitments",
        json={
            "name": "Alquiler",
            "amount": "50000.00",
            "due_date": app_today().isoformat(),
            "category": "vivienda",
        },
    )
    summary = fast_client.get(f"{API}/dashboard/summary").json()

    body = _ask(fast_client, "¿cuánto tengo disponible?")

    # 620.000 - 50.000 de compromiso - 120.000 protegidos - 40.000 de colchón = 410.000
    assert summary["available_real"] == "410000.00"
    assert "$410.000" in body["answer"]


def test_ultimos_gastos_devuelve_como_mucho_cinco(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    for index in range(7):
        _expense(fast_client, f"{(index + 1) * 1000}.00", "comida", description=f"Compra {index}")

    body = _ask(fast_client, "¿cuáles fueron mis últimos gastos?")

    assert body["answer"].count("•") == fast_path_service.MAX_ITEMS
    assert len(body["structured_answer"]["details"]) == fast_path_service.MAX_ITEMS


def test_ultimos_gastos_no_mezcla_ingresos(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _expense(fast_client, "25000.00", "transporte", description="Nafta")
    _income(fast_client, "1200000.00", description="Sueldo")

    body = _ask(fast_client, "¿cuáles fueron mis últimos gastos?")

    assert "Nafta" in body["answer"]
    assert "Sueldo" not in body["answer"]


def test_compromisos_pendientes_listados_por_vencimiento(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    today = app_today()
    for name, amount, days in (("Internet", "30000.00", 9), ("Alquiler", "350000.00", 2)):
        response = fast_client.post(
            f"{API}/commitments",
            json={
                "name": name,
                "amount": amount,
                "due_date": (today + timedelta(days=days)).isoformat(),
                "category": "servicios",
            },
        )
        assert response.status_code == 201, response.text

    body = _ask(fast_client, "¿qué compromisos tengo pendientes?")

    assert "$380.000" in body["answer"]
    # El que vence antes va primero, aunque se haya cargado después.
    assert body["answer"].index("Alquiler") < body["answer"].index("Internet")


def test_cuanto_tengo_comprometido_devuelve_solo_el_total(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    fast_client.post(
        f"{API}/commitments",
        json={
            "name": "Alquiler",
            "amount": "350000.00",
            "due_date": app_today().isoformat(),
            "category": "vivienda",
        },
    )

    body = _ask(fast_client, "¿cuánto tengo comprometido?")

    assert "$350.000" in body["answer"]
    assert "•" not in body["answer"]


def test_el_gasto_de_un_compromiso_pagado_se_computa_como_gasto(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Pagar un compromiso crea una Transaction expense real, y como tal se suma.

    Es plata que salió: si el total del mes la ignorara, el número no cerraría con el
    dashboard ni con lo que la persona ve en sus movimientos.
    """
    make_profile()
    created = fast_client.post(
        f"{API}/commitments",
        json={
            "name": "Internet",
            "amount": "30000.00",
            "due_date": app_today().isoformat(),
            "category": "servicios",
        },
    )
    assert created.status_code == 201, created.text
    paid = fast_client.patch(f"{API}/commitments/{created.json()['id']}", json={"status": "paid"})
    assert paid.status_code == 200, paid.text

    body = _ask(fast_client, "¿cuánto gasté en servicios este mes?")

    assert "$30.000" in body["answer"]


# ---------- Casos vacíos ----------


def test_sin_movimientos_responde_con_una_frase_clara(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _ask(fast_client, "¿cuánto gasté este mes?")
    assert body["answer"] == "No registraste gastos este mes."
    # Nunca "$0": no registrar nada y gastar cero no son lo mismo para quien pregunta.
    assert "$" not in body["answer"]


def test_categoria_sin_gastos_lo_dice_sin_inventar(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    _expense(fast_client, "42000.00", "comida")

    body = _ask(fast_client, "¿cuánto gasté en salud este mes?")

    assert body["answer"] == "No registraste gastos en salud este mes."


def test_sin_compromisos_pendientes(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _ask(fast_client, "¿qué compromisos tengo pendientes?")
    assert body["answer"] == "No tenés compromisos pendientes."


def test_sin_movimientos_no_lista_nada(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _ask(fast_client, "¿cuáles fueron mis últimos gastos?")
    assert body["answer"] == "Todavía no registraste gastos."


def test_sin_perfil_cae_al_agente_en_lugar_de_romper(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una cuenta sin onboarding no tiene saldo que responder: sigue el flujo de siempre.

    El fast path se aparta y no cambia lo que la persona ve hoy en ese caso.
    """
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    try:
        response = client.post(CHAT, json={"message": "¿cuál es mi saldo?"})
    finally:
        app.dependency_overrides.pop(get_draft_store, None)

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "agent"


# ---------- Aislamiento entre cuentas ----------


def test_cada_usuario_ve_solo_sus_gastos(
    client_for: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dos cuentas, dos totales. El user_id sale del JWT y no del mensaje."""
    monkeypatch.setattr(
        ai_chat_service, "build_brain", lambda settings: ExplodingBrain(), raising=True
    )
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    app.dependency_overrides[get_ai_gateway] = ExplodingProvider
    try:
        mine = client_for(TEST_USER_ID)
        mine.put(f"{API}/profile", json=default_profile_payload())
        _expense(mine, "85400.00", "comida")

        theirs = client_for(OTHER_USER_ID, OTHER_USER_EMAIL)
        theirs.put(
            f"{API}/profile", json=default_profile_payload(name="Otra", current_balance="10.00")
        )
        _expense(theirs, "7000.00", "comida")

        mine_body = _ask(client_for(TEST_USER_ID), "¿cuánto gasté este mes?")
        theirs_body = _ask(client_for(OTHER_USER_ID, OTHER_USER_EMAIL), "¿cuánto gasté este mes?")
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)

    assert "$85.400" in mine_body["answer"]
    assert "$7.000" not in mine_body["answer"]

    assert "$7.000" in theirs_body["answer"]
    assert "$85.400" not in theirs_body["answer"]


def test_el_saldo_de_cada_cuenta_es_el_suyo(
    client_for: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_chat_service, "build_brain", lambda settings: ExplodingBrain(), raising=True
    )
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    app.dependency_overrides[get_ai_gateway] = ExplodingProvider
    try:
        mine = client_for(TEST_USER_ID)
        mine.put(f"{API}/profile", json=default_profile_payload())

        theirs = client_for(OTHER_USER_ID, OTHER_USER_EMAIL)
        theirs.put(
            f"{API}/profile",
            json=default_profile_payload(name="Otra", current_balance="999.00"),
        )

        mine_body = _ask(client_for(TEST_USER_ID), "¿cuál es mi saldo?")
        theirs_body = _ask(client_for(OTHER_USER_ID, OTHER_USER_EMAIL), "¿cuál es mi saldo?")
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)

    assert "$620.000" in mine_body["answer"]
    assert "$999" in theirs_body["answer"]
    assert "$620.000" not in theirs_body["answer"]


def test_el_user_id_no_puede_viajar_en_el_mensaje() -> None:
    """Nombrar otro usuario en el texto no es una vía para leer sus datos.

    Hay dos barreras y esta prueba cubre la primera: el clasificador no extrae ningún
    identificador del mensaje, así que una consulta así ni siquiera es fast path. La
    segunda es estructural: `execute_fast_path` recibe el `user_id` como keyword-only
    desde el JWT y filtra por él en todas sus consultas, sin leer nunca el texto.
    """
    assert match_fast_path(f"¿cuánto gastó el usuario {OTHER_USER_ID} este mes?") is None
    assert match_fast_path("cuánto gasté este mes como el usuario 22222222") is None


# ---------- Sin IA, sin grafo, sin cuota ----------


def test_no_consume_la_cuota_diaria(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Lo que no llama al proveedor no puede gastar una de las 10 consultas del día."""
    make_profile()
    before = fast_client.get(USAGE).json()

    for _ in range(3):
        _ask(fast_client, "¿cuánto gasté este mes?")

    after = fast_client.get(USAGE).json()
    assert before["used"] == 0
    assert after["used"] == 0
    assert after["remaining"] == before["remaining"]


def test_no_genera_embeddings(
    fast_client: TestClient, make_profile: Callable[..., dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cero embeddings durante el turno: el fast path no consulta el RAG.

    El contador se instala DESPUÉS de cargar los datos, porque dar de alta un movimiento sí
    lo indexa (y debe seguir haciéndolo). Lo que se mide es solo la consulta.
    """
    make_profile()
    _expense(fast_client, "42000.00", "servicios")

    calls: list[str] = []
    original = MockEmbeddingProvider.embed

    def _counting(self: object, text: str) -> list[float]:
        calls.append(text)
        return original(self, text)

    monkeypatch.setattr(MockEmbeddingProvider, "embed", _counting, raising=True)

    body = _ask(fast_client, "¿cuánto gasté en servicios este mes?")

    assert body["source"] == "fast_path"
    assert calls == []


def test_la_respuesta_fast_path_no_trae_cabeceras_de_cuota(
    fast_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """No se reservó nada, así que no hay estado de cuota que publicar.

    El frontend tolera que falten (`recordUsageFromHeaders` sale temprano) y sigue
    mostrando el último valor conocido, que es el correcto: no se consumió nada.
    """
    make_profile()
    response = fast_client.post(CHAT, json={"message": "¿cuál es mi saldo?"})

    assert response.status_code == 200
    assert LIMIT_HEADER not in response.headers
    assert REMAINING_HEADER not in response.headers
    assert response.json()["usage"] is None


def test_el_fallback_conserva_cuota_y_flujo(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Una consulta compleja sigue igual que siempre: grafo, tools, cuota y cabeceras."""
    make_profile()
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    try:
        response = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})
    finally:
        app.dependency_overrides.pop(get_draft_store, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "agent"
    assert body["intent"] == AgentIntent.DASHBOARD_SUMMARY.value
    assert "get_financial_summary" in [t["name"] for t in body["tools_used"]]
    # La cuota se cobró y se informó, exactamente como antes de que existiera el fast path.
    assert response.headers[LIMIT_HEADER] == "10"
    assert body["usage"]["used"] == 1
    assert client.get(USAGE).json()["used"] == 1


def test_una_escritura_pendiente_sigue_bloqueando_el_turno(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """El 409 gana sobre el fast path: primero se resuelve la acción pendiente.

    El atajo se engancha DESPUÉS de ese chequeo justamente para no aflojar esta garantía.
    """
    make_profile()
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    try:
        first = client.post(CHAT, json={"message": "Gasté 25 lucas ayer en nafta con débito"})
        assert first.status_code == 200, first.text
        assert first.json()["requires_approval"] is True

        blocked = client.post(
            CHAT,
            json={
                "message": "¿cuánto gasté este mes?",
                "conversation_id": first.json()["conversation_id"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_draft_store, None)

    assert blocked.status_code == 409


def test_un_alta_de_compromiso_a_medias_sigue_siendo_del_agente(
    client: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Con un compromiso incompleto en curso, el turno siguiente lo interpreta el grafo."""
    make_profile()
    app.dependency_overrides[get_draft_store] = InMemoryDraftStore
    try:
        first = client.post(CHAT, json={"message": "Necesito pagar el alquiler pronto"})
        assert first.status_code == 200, first.text
        assert first.json()["intent"] == AgentIntent.CREATE_COMMITMENT.value

        second = client.post(
            CHAT,
            json={
                "message": "¿cuál es mi saldo?",
                "conversation_id": first.json()["conversation_id"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_draft_store, None)

    assert second.status_code == 200, second.text
    assert second.json()["source"] == "agent"


# ---------- Zona horaria ----------


def test_el_dia_se_corta_en_argentina_y_no_en_utc() -> None:
    """A las 02:00 UTC del 12 en Argentina todavía es 11: "hoy" es el día de acá.

    Sin esto, en Render (que corre en UTC) un gasto de las 22:00 quedaría fuera del total
    de "hoy" y aparecería en el del día siguiente.
    """
    argentina_day = app_today(datetime(2026, 8, 12, 2, 0, tzinfo=UTC))
    assert argentina_day == date(2026, 8, 11)

    start, end = period_bounds(Period.TODAY, argentina_day)
    assert start == end == date(2026, 8, 11)


def test_rangos_de_semana_y_mes() -> None:
    """Semana de lunes a domingo; mes calendario, el mismo que usa el dashboard."""
    wednesday = date(2026, 8, 12)
    assert period_bounds(Period.WEEK, wednesday) == (date(2026, 8, 10), date(2026, 8, 16))
    assert period_bounds(Period.MONTH, wednesday) == (date(2026, 8, 1), date(2026, 8, 31))


def test_un_gasto_de_la_noche_argentina_cuenta_en_su_dia(
    fast_client: TestClient, make_profile: Callable[..., dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """El total de "hoy" se arma con el día de Argentina, no con la fecha del servidor."""
    make_profile()
    today = app_today()
    _expense(fast_client, "15000.00", "comida", occurred_on=today.isoformat())

    # El servidor ya pasó a la medianoche UTC, pero en Argentina sigue siendo el mismo día.
    utc_midnight_crossed = datetime.combine(today + timedelta(days=1), datetime.min.time())
    monkeypatch.setattr(
        fast_path_service,
        "app_today",
        lambda: app_today(utc_midnight_crossed.replace(hour=2, tzinfo=UTC)),
    )

    body = _ask(fast_client, "¿cuánto gasté hoy?")

    assert "$15.000" in body["answer"]
